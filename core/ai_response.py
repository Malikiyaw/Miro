"""Canonical AI response pipeline with native tool-call preservation."""
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

class AIErrorType(str, Enum):
    OK="OK"; EMPTY_RESPONSE="EMPTY_RESPONSE"; INVALID_RESPONSE="INVALID_RESPONSE"; AUTH_ERROR="AUTH_ERROR"; RATE_LIMIT="RATE_LIMIT"; MODEL_NOT_FOUND="MODEL_NOT_FOUND"; PROVIDER_ERROR="PROVIDER_ERROR"; TIMEOUT="TIMEOUT"; NETWORK_ERROR="NETWORK_ERROR"; TOOL_CALL_FAILURE="TOOL_CALL_FAILURE"; ACTION_FAILURE="ACTION_FAILURE"

RETRYABLE={AIErrorType.RATE_LIMIT,AIErrorType.TIMEOUT,AIErrorType.NETWORK_ERROR,AIErrorType.PROVIDER_ERROR}

class ResponseKind(str, Enum):
    TEXT_RESPONSE="TEXT_RESPONSE"; TOOL_CALL_RESPONSE="TOOL_CALL_RESPONSE"; FINAL_RESPONSE="FINAL_RESPONSE"; EMPTY_RESPONSE="EMPTY_RESPONSE"; ERROR="ERROR"

def new_request_id(): return f"ai_{uuid.uuid4().hex[:8]}"

@dataclass
class AIResponse:
    text:str=""; provider:str=""; model:str=""; request_id:str=field(default_factory=new_request_id)
    status:AIErrorType=AIErrorType.OK; kind:ResponseKind=ResponseKind.TEXT_RESPONSE; finish_reason:str=""
    usage:Dict[str,int]=field(default_factory=dict); has_tool_calls:bool=False; tool_calls:List[Dict[str,Any]]=field(default_factory=list)
    raw_shape:str=""; latency_ms:float=0.0
    @property
    def ok(self): return self.status==AIErrorType.OK and (self.kind==ResponseKind.TOOL_CALL_RESPONSE or bool(self.text.strip()))
    def describe(self):
        base=f"{self.provider}/{self.model or '?'} [{self.status.value}/{self.kind.value}]"
        if self.finish_reason: base+=f" finish={self.finish_reason}"
        if self.tool_calls: base+=f" tool_calls={len(self.tool_calls)}"
        if self.usage: base+=f" tokens={self.usage.get('total_tokens','?')}"
        if self.latency_ms: base+=f" {self.latency_ms:.0f}ms"
        return base

def _text_from_parts(parts):
    if isinstance(parts,str): return parts
    if not isinstance(parts,list): return ""
    return "".join((p if isinstance(p,str) else str(p.get('text',''))) for p in parts if isinstance(p,(str,dict)))

def _usage_from(data):
    usage=data.get('usage') or {}; return {k:int(v) for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens','input_tokens','output_tokens'} and isinstance(v,(int,float))}

def _normalize_tool_calls(tool_calls):
    out=[]
    for tc in tool_calls or []:
        if not isinstance(tc,dict): continue
        fn=tc.get('function') or {}
        args=fn.get('arguments', tc.get('arguments', {}))
        if isinstance(args,str):
            import json
            try: args=json.loads(args)
            except Exception: args={"_raw":args}
        out.append({'id':tc.get('id',''),'name':str(fn.get('name') or tc.get('name') or ''),'arguments':args if isinstance(args,dict) else {}})
    return out

def normalize_provider_response(res_data, provider="", model="", request_id=None, latency_ms=0.0):
    rid=request_id or new_request_id(); r=AIResponse(provider=provider,model=model,request_id=rid,latency_ms=latency_ms)
    if not isinstance(res_data,dict): r.status=AIErrorType.INVALID_RESPONSE; r.kind=ResponseKind.ERROR; r.raw_shape='non-dict'; return r
    r.usage=_usage_from(res_data)
    choices=res_data.get('choices')
    if isinstance(choices,list) and choices:
        choice=choices[0] if isinstance(choices[0],dict) else {}; msg=choice.get('message') or {}
        r.finish_reason=str(choice.get('finish_reason') or res_data.get('stop_reason') or '')
        r.tool_calls=_normalize_tool_calls(msg.get('tool_calls') or choice.get('tool_calls') or [])
        r.has_tool_calls=bool(r.tool_calls)
        if isinstance(msg.get('content'),(str,list)): r.text=_text_from_parts(msg['content'])
        elif isinstance(choice.get('text'),str): r.text=choice['text']
        if r.has_tool_calls:
            r.raw_shape='choices[0].message.tool_calls'; r.status=AIErrorType.OK; r.kind=ResponseKind.TOOL_CALL_RESPONSE; return r
        if r.text.strip(): r.raw_shape='choices[0].message.content'; r.status=AIErrorType.OK; r.kind=ResponseKind.TEXT_RESPONSE; return r
        r.status=AIErrorType.EMPTY_RESPONSE; r.kind=ResponseKind.EMPTY_RESPONSE; r.raw_shape='choices-without-content'; return r
    if isinstance(res_data.get('content'),list):
        blocks=res_data['content']; r.tool_calls=_normalize_tool_calls([b for b in blocks if isinstance(b,dict) and b.get('type')=='tool_use'])
        r.has_tool_calls=bool(r.tool_calls); r.text=_text_from_parts(blocks); r.finish_reason=str(res_data.get('stop_reason') or '')
        if r.has_tool_calls: r.status=AIErrorType.OK; r.kind=ResponseKind.TOOL_CALL_RESPONSE; r.raw_shape='anthropic.content.tool_use'; return r
        if r.text.strip(): r.status=AIErrorType.OK; r.kind=ResponseKind.TEXT_RESPONSE; r.raw_shape='anthropic.content'; return r
        r.status=AIErrorType.EMPTY_RESPONSE; r.kind=ResponseKind.EMPTY_RESPONSE; return r
    if isinstance(res_data.get('output_text'),str):
        r.text=res_data['output_text']; r.status=AIErrorType.OK if r.text.strip() else AIErrorType.EMPTY_RESPONSE; r.kind=ResponseKind.TEXT_RESPONSE if r.text.strip() else ResponseKind.EMPTY_RESPONSE; r.raw_shape='output_text'; return r
    if isinstance(res_data.get('response'),str):
        r.text=res_data['response']; r.status=AIErrorType.OK if r.text.strip() else AIErrorType.EMPTY_RESPONSE; r.kind=ResponseKind.TEXT_RESPONSE if r.text.strip() else ResponseKind.EMPTY_RESPONSE; r.raw_shape='response:str'; return r
    r.status=AIErrorType.INVALID_RESPONSE; r.kind=ResponseKind.ERROR; r.raw_shape=f"unrecognized:{list(res_data.keys())[:6]}"; return r

def classify_http_error(status,body=""):
    b=(body or '').lower()
    if status in (401,403): return AIErrorType.AUTH_ERROR
    if status==429:return AIErrorType.RATE_LIMIT
    if status==404:return AIErrorType.MODEL_NOT_FOUND if 'model' in b else AIErrorType.PROVIDER_ERROR
    if status==408:return AIErrorType.TIMEOUT
    if 500<=status<=599:return AIErrorType.PROVIDER_ERROR
    return AIErrorType.MODEL_NOT_FOUND if status==400 and 'model' in b else AIErrorType.PROVIDER_ERROR

ERROR_MARKERS=("i'm sorry","i cannot fulfill","as an ai language model","[error]","internal server error","<html")
def watchdog_check(text):
    if not text or not text.strip(): return False,'blank content'
    s=text.strip(); low=s.lower()
    for marker in ERROR_MARKERS:
        if marker in low and len(s)<200:return False,f'error marker: {marker}'
    return True,''
