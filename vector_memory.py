import os
import time
import json
import logging
import math
from typing import List, Dict, Any, Optional
import hashlib
import asyncio
from datetime import datetime, timedelta

try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False

logger = logging.getLogger(__name__)

class VectorMemory:
    """
    Persistent long-term memory using Chroma DB for vector storage.
    Stores and retrieves conversations based on semantic similarity.
    Includes fallback to keyword search if ChromaDB fails.
    """
    
    def __init__(self, persist_directory: str = "vector_db"):
        self.persist_directory = persist_directory
        self.client = None
        self.collection = None
        self._fallback_enabled = False
        self._keyword_index = {}  # fallback: word -> list of (doc_id, content)
        self._last_retry = 0
        self._retry_interval = 300  # Retry ChromaDB every 5 minutes if it fails
        self._initialize()
    
    def _initialize(self):
        """Initialize Chroma DB client and collection."""
        if not CHROMADB_AVAILABLE:
            logger.warning("ChromaDB not available. Using fallback keyword search.")
            self._fallback_enabled = True
            return
            
        try:
            # Shared infrastructure for sharding
            # Check for remote ChromaDB configuration
            chroma_host = os.getenv("CHROMA_HOST")
            chroma_port = os.getenv("CHROMA_PORT", "8000")
            
            if chroma_host:
                logger.info(f"Connecting to remote ChromaDB at {chroma_host}:{chroma_port}")
                self.client = chromadb.HttpClient(
                    host=chroma_host,
                    port=chroma_port,
                    settings=Settings(anonymized_telemetry=False)
                )
            else:
                # Ensure directory exists for local persistent storage
                os.makedirs(self.persist_directory, exist_ok=True)

                # Initialize client
                self.client = chromadb.PersistentClient(
                    path=self.persist_directory,
                    settings=Settings(
                        anonymized_telemetry=False,
                        allow_reset=True
                    )
                )
            
            # Get or create collection for conversations
            self.collection = self.client.get_or_create_collection(
                name="conversations",
                metadata={"hnsw:space": "cosine"}
            )
            
            # Get or create collection for conversation summaries
            self.summary_collection = self.client.get_or_create_collection(
                name="conversation_summaries",
                metadata={"hnsw:space": "cosine"}
            )
            
            self._load_keyword_index()
            
        except Exception as e:
            logger.error("ChromaDB initialization failed: %s. Using fallback keyword search.", e)
            self._fallback_enabled = True
        finally:
            self._last_retry = time.time() if hasattr(time, 'time') else datetime.now().timestamp()
    
    def _load_keyword_index(self):
        """Load keyword index from disk for fallback."""
        index_file = os.path.join(self.persist_directory, "keyword_index.json")
        if os.path.exists(index_file):
            try:
                with open(index_file, "r") as f:
                    self._keyword_index = json.load(f)
            except Exception:
                self._keyword_index = {}
    
    def _save_keyword_index(self):
        """Save keyword index to disk."""
        index_file = os.path.join(self.persist_directory, "keyword_index.json")
        try:
            temp_path = index_file + ".tmp"
            with open(temp_path, "w") as f:
                json.dump(self._keyword_index, f)
            os.replace(temp_path, index_file)
        except Exception as e:
            logger.error("Failed to save keyword index: %s", e)
    
    def _add_to_keyword_index(self, doc_id: str, content: str, metadata: dict):
        """Add document to keyword index."""
        words = content.lower().split()
        for word in set(words):
            if word not in self._keyword_index:
                self._keyword_index[word] = []
            self._keyword_index[word].append({
                "id": doc_id,
                "content": content,
                "metadata": metadata
            })
        self._save_keyword_index()
    
    def _keyword_search(self, guild_id: int, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """Fallback keyword-based search when ChromaDB is unavailable."""
        query_words = query.lower().split()
        scored = {}
        
        for word in query_words:
            if word in self._keyword_index:
                for item in self._keyword_index[word]:
                    if str(item["metadata"].get("guild_id")) != str(guild_id):
                        continue
                    
                    doc_id = item["id"]
                    if doc_id not in scored:
                        scored[doc_id] = {
                            "document": item["content"],
                            "metadata": item["metadata"],
                            "score": 0
                        }
                    scored[doc_id]["score"] += 1
        
        sorted_results = sorted(scored.values(), key=lambda x: x["score"], reverse=True)[:n_results]
        return [{"id": r["metadata"].get("doc_id", ""), "document": r["document"], "metadata": r["metadata"], "distance": 1.0 - (r["score"] / max(len(query_words), 1))} for r in sorted_results]
    
    def is_healthy(self) -> bool:
        """Check if vector memory is operational."""
        return not self._fallback_enabled and self.client is not None and self.collection is not None

    def _check_reconnect(self):
        """Periodically attempt to reconnect to ChromaDB if in fallback mode."""
        if not self._fallback_enabled:
            return
            
        now = datetime.now().timestamp()
        if now - self._last_retry > self._retry_interval:
            logger.info("Attempting to reconnect to ChromaDB...")
            self._fallback_enabled = False  # Reset to try initialization
            self._initialize()
            if not self._fallback_enabled:
                logger.info("Successfully reconnected to ChromaDB.")
    
    def _generate_id(self, guild_id: int, user_id: int, timestamp: float) -> str:
        """Generate a unique ID for a conversation entry."""
        data = f"{guild_id}_{user_id}_{timestamp}"
        return hashlib.md5(data.encode()).hexdigest()
    
    async def store_conversation(
        self, 
        guild_id: int, 
        user_id: int, 
        user_message: str, 
        bot_response: str,
        reasoning: str = "",
        walkthrough: str = "",
        importance_score: float = 0.5
    ):
        """
        Store a conversation exchange in vector memory.
        
        Args:
            guild_id: Discord guild ID
            user_id: Discord user ID
            user_message: User's message
            bot_response: Bot's response
            reasoning: AI's reasoning process
            walkthrough: AI's planned walkthrough
            importance_score: Calculated importance (0.0-1.0)
        """
        if not self.client or not self.collection:
            return
            
        try:
            timestamp = datetime.now().timestamp()
            doc_id = self._generate_id(guild_id, user_id, timestamp)
            
            # Create document text for embedding
            document_text = f"""
User: {user_message}
Assistant: {bot_response}
Reasoning: {reasoning}
Walkthrough: {walkthrough}
            """.strip()
            
            # Metadata for filtering and retrieval
            metadata = {
                "guild_id": str(guild_id),
                "user_id": str(user_id),
                "timestamp": timestamp,
                "importance_score": importance_score,
                "has_reasoning": bool(reasoning),
                "has_walkthrough": bool(walkthrough),
                "access_count": 0,
                "last_accessed": timestamp
            }
            
            # Store in collection — run in executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            coll = self.collection
            doc_text = document_text
            meta = metadata
            did = doc_id
            await loop.run_in_executor(
                None,
                lambda: coll.add(
                    documents=[doc_text],
                    metadatas=[meta],
                    ids=[did]
                )
            )
            
            logger.debug("Stored conversation in vector memory: %s", doc_id)
            
            if self._fallback_enabled:
                self._add_to_keyword_index(doc_id, document_text, metadata)
            
        except Exception as e:
            logger.error("Failed to store conversation in vector memory: %s", e)
    
    def _rebuild_keyword_index(self):
        """Rebuild keyword index from collection (for migration to fallback)."""
        if not self.collection:
            return
        
        try:
            all_data = self.collection.get()
            for i, doc_id in enumerate(all_data.get("ids", [])):
                content = all_data.get("documents", [""])[i]
                metadata = all_data.get("metadatas", [{}])[i]
                self._add_to_keyword_index(doc_id, content, metadata)
            logger.info("Keyword index rebuilt with %d entries", len(self._keyword_index))
        except Exception as e:
            logger.error("Failed to rebuild keyword index: %s", e)
    
    async def retrieve_relevant_conversations(
        self,
        guild_id: int,
        user_id: int,
        query: str,
        n_results: int = 5,
        min_importance: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        Retrieve conversations relevant to the current query.
        Falls back to keyword search if ChromaDB is unavailable.
        
        Args:
            guild_id: Discord guild ID (for filtering)
            user_id: Discord user ID (for filtering)
            query: Current user query to find similar conversations
            n_results: Maximum number of results to return
            min_importance: Minimum importance score threshold
            
        Returns:
            List of relevant conversation dictionaries
        """
        self._check_reconnect()
        
        if self._fallback_enabled:
            return self._keyword_search(guild_id, query, n_results)
        
        if not self.client or not self.collection:
            return self._keyword_search(guild_id, query, n_results)
            
        try:
            # Build where clause for filtering
            where_conditions = []
            
            # Always filter by guild for server-specific memory
            where_conditions.append({"guild_id": {"$eq": str(guild_id)}})
            
            # Optionally filter by user for personal memory
            # where_conditions.append({"user_id": {"$eq": str(user_id)}})
            
            # Filter by minimum importance
            where_conditions.append({"importance_score": {"$gte": min_importance}})
            
            # Combine conditions (ChromaDB uses AND for multiple conditions)
            where_clause = {"$and": where_conditions} if len(where_conditions) > 1 else where_conditions[0]
            
            # Query the collection — run in executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            collection = self.collection
            results = await loop.run_in_executor(
                None,
                lambda: collection.query(
                    query_texts=[query],
                    n_results=n_results,
                    where=where_clause,
                    include=["documents", "metadatas", "distances"]
                )
            )
            
            # Format results
            conversations = []
            if results['ids'] and len(results['ids'][0]) > 0:
                # Reinforce accessed memories by updating access count and boosting importance
                to_update = []
                for i in range(len(results['ids'][0])):
                    mem_id = results['ids'][0][i]
                    metadata = results['metadatas'][0][i]
                    
                    # Update access tracking
                    access_count = int(metadata.get('access_count', '0')) + 1
                    metadata['access_count'] = str(access_count)
                    metadata['last_accessed'] = str(datetime.now().timestamp())
                    
                    # Apply reinforcement: boost importance based on access frequency
                    # Using logarithmic scaling to prevent runaway importance
                    importance = metadata.get('importance_score', 0.5)
                    # Reinforcement factor: more access = higher importance, but with diminishing returns
                    reinforcement_factor = min(0.5, 0.1 * math.log(access_count + 1))
                    # Boost importance but cap at 1.0
                    new_importance = min(1.0, importance + reinforcement_factor)
                    metadata['importance_score'] = str(new_importance)
                    
                    to_update.append({
                        'id': mem_id,
                        'metadata': metadata
                    })
                    
                    conv = {
                        "id": mem_id,
                        "document": results['documents'][0][i],
                        "metadata": metadata,
                        "similarity": 1 - results['distances'][0][i]  # Convert distance to similarity
                    }
                    conversations.append(conv)
                
                # Update the reinforced memories in the database
                for update in to_update:
                    try:
                        self.collection.update(
                            ids=[update['id']],
                            metadatas=[update['metadata']]
                        )
                    except Exception as e:
                        logger.error("Failed to update memory %s after reinforcement: %s", update['id'], e)
            
            logger.debug("Retrieved %d relevant conversations from vector memory", len(conversations))
            return conversations
            
        except Exception as e:
            logger.error("Failed to retrieve conversations from vector memory: %s", e)
            return []
    
    def decay_memory(self, half_life_days: float = 7.0, max_age_days: int = 30):
        """
        Apply memory decay using half-life formula to forget old, low-importance memories.
        
        Args:
            half_life_days: Number of days for a memory to lose half its importance
            max_age_days: Maximum age in days before considering for deletion
        """
        if not self.client or not self.collection:
            return
            
        try:
            # Get all memories with metadata
            results = self.collection.get(
                include=["metadatas"]
            )
            
            if not results['ids']:
                return
                
            current_time = datetime.now().timestamp()
            max_age_seconds = max_age_days * 24 * 3600
            
            # Identify memories to decay or remove
            to_delete = []
            to_update = []
            
            for i, mem_id in enumerate(results['ids']):
                metadata = results['metadatas'][i]
                timestamp = float(metadata.get('timestamp', 0))
                importance = float(metadata.get('importance_score', 0.5))
                is_pinned = metadata.get('is_pinned', 'false').lower() == 'true'
                
                # Skip pinned memories (they don't decay)
                if is_pinned:
                    continue
                    
                age_seconds = current_time - timestamp
                age_days = age_seconds / (24 * 3600)
                
                # Calculate decay factor based on half-life formula
                # Importance halves every half_life_days
                if half_life_days > 0:
                    decay_factor = importance * (0.5 ** (age_days / half_life_days))
                else:
                    decay_factor = importance  # No decay if half_life is 0 or negative
                
                # If decayed below threshold or too old, mark for deletion
                if decay_factor < 0.1 or age_days > max_age_days:
                    to_delete.append(mem_id)
                elif decay_factor < importance:
                    # Update importance score with decayed value
                    to_update.append({
                        'id': mem_id,
                        'metadata': {**metadata, 'importance_score': str(decay_factor)}
                    })
            
            # Delete expired memories
            if to_delete:
                self.collection.delete(ids=to_delete)
                logger.info("Decayed %d old memories from vector memory", len(to_delete))
            
            # Update importance scores for decaying memories
            for update in to_update:
                self.collection.update(
                    ids=[update['id']],
                    metadatas=[update['metadata']]
                )
                
            logger.debug("Applied memory decay: %d deleted, %d updated", len(to_delete), len(to_update))
            
        except Exception as e:
            logger.error("Failed to apply memory decay: %s", e)
    
    def summarize_memories(self, guild_id: int, user_id: Optional[int] = None, 
                          max_memories: int = 10, similarity_threshold: float = 0.7):
        """
        Summarize similar memories to reduce storage and improve retrieval.
        
        Args:
            guild_id: Guild ID to filter memories
            user_id: Optional user ID for personal memories
            max_memories: Maximum number of memories to consider for summarization
            similarity_threshold: Threshold for considering memories similar enough to summarize
        """
        if not self.client or not self.collection or not self.summary_collection:
            return
            
        try:
            # Get recent memories for this guild/user
            where_conditions = [{"guild_id": {"$eq": str(guild_id)}}]
            if user_id:
                where_conditions.append({"user_id": {"$eq": str(user_id)}})
                
            where_clause = {"$and": where_conditions} if len(where_conditions) > 1 else where_conditions[0]
            
            results = self.collection.query(
                query_texts=["recent conversation"],  # Generic query to get memories
                n_results=max_memories,
                where=where_clause,
                include=["documents", "metadatas", "distances"]
            )
            
            if not results['ids'] or len(results['ids'][0]) < 2:
                return  # Need at least 2 memories to summarize
                
            # Group similar memories
            memories = []
            for i in range(len(results['ids'][0])):
                mem_id = results['ids'][0][i]
                document = results['documents'][0][i]
                metadata = results['metadatas'][0][i]
                similarity = 1 - results['distances'][0][i]  # Convert distance to similarity
                
                memories.append({
                    'id': mem_id,
                    'document': document,
                    'metadata': metadata,
                    'similarity': similarity
                })
            
            # Simple summarization: combine memories with high similarity
            summarized_groups = []
            used_indices = set()
            
            for i, mem1 in enumerate(memories):
                if i in used_indices:
                    continue
                    
                group = [mem1]
                used_indices.add(i)
                
                for j, mem2 in enumerate(memories[i+1:], i+1):
                    if j in used_indices:
                        continue
                        
                    # Simple similarity check based on shared keywords
                    # In a real implementation, you'd use embedding similarity
                    if self._calculate_text_similarity(mem1['document'], mem2['document']) > similarity_threshold:
                        group.append(mem2)
                        used_indices.add(j)
                
                summarized_groups.append(group)
            
            # Create summaries for each group
            for group in summarized_groups:
                if len(group) < 2:
                    continue  # Skip single memories
                    
                # Create a summary document
                combined_text = " ".join([mem['document'] for mem in group])
                summary_prompt = f"Summarize the following conversation memories concisely:\n\n{combined_text[:1000]}"
                
                # For now, create a simple extractive summary
                # In production, you'd use the AI client to generate this
                summary_text = f"Summary of {len(group)} related conversations: {combined_text[:200]}..."
                
                # Generate ID for summary
                timestamp = datetime.now().timestamp()
                summary_id = self._generate_id(guild_id, user_id or 0, timestamp) + "_summary"
                
                # Store summary
                summary_metadata = {
                    "guild_id": str(guild_id),
                    "user_id": str(user_id) if user_id else "unknown",
                    "timestamp": str(timestamp),
                    "is_summary": "true",
                    "source_count": str(len(group)),
                    "importance_score": str(sum([float(m['metadata'].get('importance_score', 0.5)) for m in group]) / len(group))
                }
                
                self.summary_collection.add(
                    documents=[summary_text],
                    metadatas=[summary_metadata],
                    ids=[summary_id]
                )
                
                # Optionally delete original memories after summarization
                # For safety, we'll just log this action
                logger.info("Created summary for %d memories in guild %d", len(group), guild_id)
                
        except Exception as e:
            logger.error("Failed to summarize memories: %s", e)
    
    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate simple text similarity based on shared words.
        This is a placeholder - in production you'd use embeddings.
        """
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
            
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0
    
    def pin_memory(self, memory_id: str, guild_id: int, user_id: int) -> bool:
        """
        Pin/flag a memory as important to prevent decay.
        
        Args:
            memory_id: ID of the memory to pin
            guild_id: Guild ID for verification
            user_id: User ID for verification
            
        Returns:
            True if successfully pinned, False otherwise
        """
        if not self.client or not self.collection:
            return False
            
        try:
            # First, get the memory to verify ownership
            results = self.collection.get(
                ids=[memory_id],
                include=["metadatas"]
            )
            
            if not results['ids'] or not results['metadatas']:
                logger.warning("Memory %s not found for pinning", memory_id)
                return False
                
            metadata = results['metadatas'][0]
            
            # Verify ownership
            if (metadata.get('guild_id') != str(guild_id) or 
                metadata.get('user_id') != str(user_id)):
                logger.warning("Ownership mismatch for memory %s", memory_id)
                return False
            
            # Update metadata to mark as pinned
            metadata['is_pinned'] = 'true'
            
            self.collection.update(
                ids=[memory_id],
                metadatas=[metadata]
            )
            
            logger.info("Pinned memory %s for user %d in guild %d", memory_id, user_id, guild_id)
            return True
            
        except Exception as e:
            logger.error("Failed to pin memory %s: %s", memory_id, e)
            return False
    
    def unpin_memory(self, memory_id: str, guild_id: int, user_id: int) -> bool:
        """
        Unpin a memory, allowing it to decay normally.
        
        Args:
            memory_id: ID of the memory to unpin
            guild_id: Guild ID for verification
            user_id: User ID for verification
            
        Returns:
            True if successfully unpinned, False otherwise
        """
        if not self.client or not self.collection:
            return False
            
        try:
            # First, get the memory to verify ownership
            results = self.collection.get(
                ids=[memory_id],
                include=["metadatas"]
            )
            
            if not results['ids'] or not results['metadatas']:
                logger.warning("Memory %s not found for unpinning", memory_id)
                return False
                
            metadata = results['metadatas'][0]
            
            # Verify ownership
            if (metadata.get('guild_id') != str(guild_id) or 
                metadata.get('user_id') != str(user_id)):
                logger.warning("Ownership mismatch for memory %s", memory_id)
                return False
            
            # Update metadata to remove pin
            metadata['is_pinned'] = 'false'
            
            self.collection.update(
                ids=[memory_id],
                metadatas=[metadata]
            )
            
            logger.info("Unpinned memory %s for user %d in guild %d", memory_id, user_id, guild_id)
            return True
            
        except Exception as e:
            logger.error("Failed to unpin memory %s: %s", memory_id, e)
            return False
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about the vector memory."""
        if not self.client or not self.collection:
            return {"status": "disabled", "count": 0}
            
        try:
            count = self.collection.count()
            summary_count = self.summary_collection.count() if self.summary_collection else 0
            
            # Get pinned memories count
            pinned_count = 0
            if self.collection:
                try:
                    pinned_results = self.collection.get(
                        where={"is_pinned": {"$eq": "true"}},
                        include=["metadatas"]
                    )
                    pinned_count = len(pinned_results['ids']) if pinned_results['ids'] else 0
                except Exception as e:
                    logger.debug(f"Failed to get pinned memories count: {e}")
                    pinned_count = 0
            
            return {
                "status": "active",
                "count": count,
                "summary_count": summary_count,
                "pinned_count": pinned_count,
                "persist_directory": self.persist_directory
            }
        except Exception as e:
            logger.error("Failed to get vector memory stats: %s", e)
            return {"status": "error", "count": 0, "error": str(e)}


# Global vector memory instance
vector_memory = VectorMemory()