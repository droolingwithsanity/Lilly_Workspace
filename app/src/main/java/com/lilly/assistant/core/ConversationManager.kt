package com.lilly.assistant.core

import com.lilly.assistant.database.ConversationDao
import com.lilly.assistant.models.Conversation
import kotlinx.coroutines.*

class ConversationManager(private val conversationDao: ConversationDao) {
    
    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    
    suspend fun getAllConversations(): List<Conversation> {
        return withContext(Dispatchers.Default) {
            try {
                conversationDao.getAllConversations()
            } catch (e: Exception) {
                emptyList()
            }
        }
    }
    
    suspend fun getConversationById(id: String): Conversation? {
        return withContext(Dispatchers.Default) {
            try {
                conversationDao.getConversationById(id)
            } catch (e: Exception) {
                null
            }
        }
    }
    
    suspend fun createConversation(conversation: Conversation): Conversation {
        return withContext(Dispatchers.Default) {
            try {
                conversationDao.insertConversation(conversation)
                conversation
            } catch (e: Exception) {
                throw e
            }
        }
    }
    
    suspend fun updateConversation(conversation: Conversation): Boolean {
        return withContext(Dispatchers.Default) {
            try {
                conversationDao.updateConversation(conversation)
                true
            } catch (e: Exception) {
                false
            }
        }
    }
    
    suspend fun deleteConversation(id: String): Boolean {
        return withContext(Dispatchers.Default) {
            try {
                val conversation = conversationDao.getConversationById(id)
                conversation?.let {
                    conversationDao.deleteConversation(it)
                    true
                } ?: false
            } catch (e: Exception) {
                false
            }
        }
    }
    
    suspend fun deleteAllConversations(): Boolean {
        return withContext(Dispatchers.Default) {
            try {
                conversationDao.deleteAllConversations()
                true
            } catch (e: Exception) {
                false
            }
        }
    }
    
    suspend fun getConversationCount(): Int {
        return withContext(Dispatchers.Default) {
            try {
                conversationDao.getAllConversations().size
            } catch (e: Exception) {
                0
            }
        }
    }
    
    suspend fun searchConversations(query: String): List<Conversation> {
        return withContext(Dispatchers.Default) {
            try {
                conversationDao.getAllConversations().filter { conversation ->
                    conversation.messages.any { message ->
                        message.content.contains(query, ignoreCase = true)
                    }
                }
            } catch (e: Exception) {
                emptyList()
            }
        }
    }
    
    fun shutdown() {
        scope.cancel()
    }
}