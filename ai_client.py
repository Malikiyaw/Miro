import os
import json
import aiohttp
import logging
import re
import asyncio
import time
from typing import List, Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, retry_if_not_exception_type
from history_manager import history_manager
from vector_memory import vector_memory
from actions import ActionHandler
from blueprints import BLUEPRINT
from ai_providers import AIProviderRegistry
from core.ai_response import new_request_id, classify_http_error, normalize_provider_response, ResponseKind, AIErrorType, watchdog_check

logger = logging.getLogger(__name__)

# Long-conversation memory: 50+ exchanges kept per guild/user (plan requirement).
# Override with MEMORY_DEPTH env var; per-guild ai_config.memory_depth wins too.
MEMORY_DEPTH_DEFAULT = int(os.getenv("MEMORY_DEPTH", "50"))

class AIClientError(Exception):
    def __init__(self, status: int, message: str, error_type=None):
        self.status=status; self.message=message; self.error_type=error_type
        super().__init__(f"AI API Client Error ({status}): {message}")

def is_retryable_exception(exception):
    if isinstance(exception, AIClientError): return exception.status >= 500 or exception.status == 429
    if isinstance(exception,(KeyError,IndexError,TypeError,AttributeError,json.JSONDecodeError,ValueError)): return False
    return True

class AIClient:
    """Provider client. Native tool calls are preserved as first-class agent turns."""
    def __init__(self, bot, api_key: str, provider: str=None, model: Optional[str]=None):
        self.bot=bot; self.default_api_key=api_key; self.default_provider=provider or os.getenv('AI_PROVIDER','openrouter'); self.model=model or os.getenv('AI_MODEL','openai/gpt-3.5-turbo')
        self.base_urls={'openrouter':os.getenv('OPENROUTER_URL','https://openrouter.ai/api/v1/chat/completions'),'openai':os.getenv('OPENAI_URL','https://api.openai.com/v1/chat/completions'),'gemini':os.getenv('GEMINI_URL','https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),'anthropic':os.getenv('ANTHROPIC_URL','https://api.anthropic.com/v1/messages'),'groq':os.getenv('GROQ_URL','https://api.groq.com/openai/v1/chat/completions'),'mistral':os.getenv('MISTRAL_URL','https://api.mistral.ai/v1/chat/completions'),'deepseek':os.getenv('DEEPSEEK_URL','https://api.deepseek.com/v1/chat/completions'),'dashscope':os.getenv('DASHSCOPE_URL','https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions'),'qwen':os.getenv('DASHSCOPE_URL','https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions'),'cerebras':os.getenv('CEREBRAS_URL','https://api.cerebras.ai/v1/chat/completions'),'sambanova':os.getenv('SAMBANOVA_URL','https://api.sambanova.ai/v1/chat/completions'),'together':os.getenv('TOGETHER_URL','https://api.together.xyz/v1/chat/completions')}
        self.consecutive_failures=0; self.last_success_ts=0.0

    def _get_guild_api_key(self,guild_id):
        from data_manager import dm
        cfg=dm.get_guild_api_key(guild_id); active=dm.get_guild_data(guild_id,'active_provider',None); stored=cfg.get('provider') if cfg else None; provider=active or stored or self.default_provider
        if cfg and provider:
            x=cfg.get('providers',{}).get(provider)
            if isinstance(x,dict) and x.get('api_key'): return x['api_key'],provider
        return self.default_api_key,provider

    def _get_all_guild_keys(self,guild_id):
        from data_manager import dm
        api=dm.load_json('guild_api_keys',default={}); gd=api.get(str(guild_id),{})
        def valid(k): return bool(k and len(k)>10 and not any(x in k.upper() for x in ('YOUR_','REPLACE_')))
        out=[]; primary=self._get_guild_api_key(guild_id)
        if valid(primary[0]): out.append({'api_key':primary[0],'provider':primary[1]})
        providers=gd.get('providers',{})
        if isinstance(providers,dict):
            for p in sorted(providers,key=lambda x:x!=primary[1]):
                if p!=primary[1]:
                    r=dm.get_guild_api_key(guild_id,provider=p)
                    if isinstance(r,dict) and valid(r.get('api_key')): out.append({'api_key':r['api_key'],'provider':p})
        return out

    def _coerce_model_for_provider(self,guild_id,provider):
        from data_manager import dm
        custom=dm.get_guild_data(guild_id,'custom_model',None); reg=AIProviderRegistry()
        if not custom:return None
        if not reg.is_chat_model(custom): return reg.default_model(provider)
        return custom

    async def chat(self,guild_id:int,user_id:int,user_input:str,system_prompt:str,persist:bool=False,extra_messages:Optional[List[Dict[str,str]]]=None)->Dict[str,Any]:
        keys=self._get_all_guild_keys(guild_id)
        if not keys:
            logger.info(f"[AI CONFIG] guild={guild_id} provider=none status=NOT_CONFIGURED")
            return {'error':'AI_NOT_CONFIGURED','summary':'AI is not configured for this server.'}
        for bundle in keys:
            try:
                result=await self._chat_internal(guild_id,user_id,user_input,system_prompt,bundle['api_key'],bundle['provider'],model_override=self._coerce_model_for_provider(guild_id,bundle['provider']),extra_messages=extra_messages)
                if result.get('_miro_empty'): continue
                self.report_success()
                if persist:
                    await self._persist_exchange(guild_id,user_id,user_input,result)
                return result
            except AIClientError as e:
                logger.warning(f"[AI FALLBACK] provider={bundle['provider']} status={e.status}")
                if e.status not in (401,403,429): raise
            except Exception:
                self.report_failure(); raise
        raise AIClientError(503,'All configured AI providers failed')

    @staticmethod
    async def _persist_exchange(guild_id:int,user_id:int,user_input:str,result:Dict[str,Any]):
        """Store the exchange so long conversations never lose the user."""
        try:
            reply=str((result or {}).get('summary') or (result or {}).get('content') or '')[:4000]
            if not guild_id or not user_id or not reply.strip():
                return
            await history_manager.add_exchange(guild_id,user_id,str(user_input)[:2000],reply)
        except Exception as e:
            logger.warning(f"failed to persist conversation exchange: {e}")

    async def _chat_internal(self,guild_id,user_id,user_input,system_prompt,api_key,provider,enhanced_input=None,model_override=None,extra_messages=None):
        from data_manager import dm
        model=model_override or dm.get_guild_data(guild_id,'custom_model',self.model) or self.model
        if not AIProviderRegistry.is_chat_model(model): model=AIProviderRegistry().default_model(provider)
        messages=[{'role':'system','content':system_prompt}]
        # ---- LONG-CONVERSATION MEMORY (50+ exchanges) ----
        # Per-guild ai_config.memory_depth wins, then MEMORY_DEPTH env, then 50.
        try:
            from core.guild_ai_config import GuildAIConfig
            depth=int(GuildAIConfig.load(guild_id).memory_depth or MEMORY_DEPTH_DEFAULT)
        except Exception:
            depth=MEMORY_DEPTH_DEFAULT
        depth=max(10,min(depth,200))
        try:
            history=await history_manager.get_enhanced_context(guild_id,user_id,depth=depth)
            if history:
                messages.extend(history[-depth*2:])
                logger.debug(f"[AI {guild_id}/{user_id}] injected {len(history)} history messages (depth={depth})")
        except Exception as e:
            logger.warning(f"history context unavailable: {e}")
        if extra_messages:
            for m in extra_messages:
                role=m.get('role','user'); content=str(m.get('content',''))
                if content: messages.append({'role':role if role in ('user','assistant','tool') else 'user','content':content})
        messages.append({'role':'user','content':enhanced_input or user_input})
        payload={'model':model,'messages':messages,'temperature':0.2,'max_tokens':8000}
        if provider in {'openai','openrouter','gemini','groq','mistral','deepseek','qwen','dashscope','cerebras','sambanova','together'}:
            # Native tools are supplied by the V9 planner/executor bridge when available.
            tools=getattr(self.bot,'agent_tool_schemas',None)
            if tools:
                payload['tools']=tools
                payload['tool_choice']='auto'
        if provider=='anthropic':
            system=messages[0]['content']; payload={'model':model,'system':system,'messages':messages[1:],'max_tokens':8000}
            tools=getattr(self.bot,'agent_tool_schemas',None)
            if tools: payload['tools']=tools; payload['tool_choice']={'type':'auto'}
            headers={'x-api-key':api_key.strip(),'anthropic-version':'2023-06-01','Content-Type':'application/json'}
        else:
            headers={'Authorization':f'Bearer {api_key.strip()}','Content-Type':'application/json'}
            if provider=='openrouter': headers.update({'HTTP-Referer':'https://github.com/antigravity','X-Title':'Miro AI Discord Bot'})
        timeout=aiohttp.ClientTimeout(total=120,connect=10); rid=new_request_id(); started=time.perf_counter(); url=self.base_urls.get(provider)
        if not url: raise AIClientError(400,f'Unsupported provider: {provider}')
        async with aiohttp.ClientSession(headers=headers,timeout=timeout) as session:
            async with session.post(url,json=payload,allow_redirects=False) as resp:
                latency=(time.perf_counter()-started)*1000
                if resp.status!=200:
                    body=await resp.text(); et=classify_http_error(resp.status,body)
                    logger.error(f"[AI ERROR] provider={provider} model={model} status={resp.status} request_id={rid} reason={body[:500]}")
                    raise AIClientError(resp.status,body,error_type=et)
                return await self._parse_and_handle_response(session,provider,url,payload,messages,resp,request_id=rid,started=started)

    async def _parse_and_handle_response(self,session,provider,provider_url,payload,messages,resp,request_id=None,started=None):
        data=await resp.json(); latency=(time.perf_counter()-started)*1000 if started else 0; model=payload.get('model','')
        normalized=normalize_provider_response(data,provider,model,request_id,latency)
        logger.info(f"[AI {request_id}] {normalized.describe()}")
        # CRITICAL: tool_calls are valid even when content is empty. Never regenerate them.
        if normalized.kind==ResponseKind.TOOL_CALL_RESPONSE:
            return {'summary':normalized.text,'content':normalized.text,'tool_calls':normalized.tool_calls,'finish_reason':normalized.finish_reason,'provider':provider,'model':model,'request_id':request_id,'_ai_response':normalized,'actions':[]}
        if not normalized.text.strip():
            logger.warning(f"[AI {request_id}] true blank response finish={normalized.finish_reason or '?'} shape={normalized.raw_shape}")
            return {'summary':'','content':'','tool_calls':[],'finish_reason':normalized.finish_reason,'provider':provider,'model':model,'request_id':request_id,'_ai_response':normalized,'_miro_empty':True,'actions':[]}
        ok,why=watchdog_check(normalized.text)
        if not ok: return {'summary':'','content':'','tool_calls':[],'finish_reason':normalized.finish_reason,'_ai_response':normalized,'_miro_empty':True,'actions':[]}
        parsed=self.extract_json(normalized.text)
        if not isinstance(parsed,dict): parsed={'summary':normalized.text}
        parsed.setdefault('summary',normalized.text); parsed.setdefault('actions',[]); parsed['content']=normalized.text; parsed['tool_calls']=[]; parsed['finish_reason']=normalized.finish_reason; parsed['_ai_response']=normalized; return parsed

    def report_success(self): self.consecutive_failures=0; self.last_success_ts=time.time()
    def report_failure(self): self.consecutive_failures=getattr(self,'consecutive_failures',0)+1

    def extract_json(self,text):
        if not text:return {'summary':''}
        try:
            parsed=json.loads(text.strip())
            if self._validate_json_response(parsed):return parsed
        except Exception:pass
        m=re.search(r'```(?:json)?\s*(\{.*?\})\s*```',text,re.DOTALL)
        if m:
            try:
                parsed=json.loads(m.group(1));
                if self._validate_json_response(parsed):return parsed
            except Exception:pass
        return {'summary':text.strip()}

    def _validate_json_response(self,data):
        if not isinstance(data,dict) or not data:return False
        if 'summary' in data and not isinstance(data['summary'],str):return False
        if 'actions' in data and not isinstance(data['actions'],list):return False
        return True

    async def generate_response(self,messages,guild_id,user_id,max_tokens=1000):
        result=await self._chat_internal(guild_id,user_id,messages[-1].get('content','') if messages else '',SYSTEM_PROMPT)
        return str(result.get('summary') or '')

SYSTEM_PROMPT="""You are Miro. For normal conversation answer naturally. For server-changing requests use the supplied native tools. Never claim a mutation happened unless the runtime reports a successful verified result. The runtime, not your text, is the source of execution truth."""
