package com.lilly.assistant.core

import com.lilly.assistant.database.LillyDatabase
import com.lilly.assistant.models.*
import kotlinx.coroutines.*

class LillyCore(
    private val database: LillyDatabase,
    private val memoryManager: MemoryManager,
    val systemPromptManager: SystemPromptManager,
    private val cursorController: CursorController,
    private val realtimeProcessor: RealtimeProcessor,
    private val skillManager: SkillManager,
    private val packageManager: LillyPackageManager
) {
    
    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    
    init {
        initialize()
    }
    
    private fun initialize() {
        scope.launch {
            // Initialize core components
            realtimeProcessor.initialize()
            cursorController.initialize()
            skillManager.initialize()
            packageManager.initialize()
        }
    }
    
    suspend fun processInput(input: UserInput): LillyResponse {
        return withContext(Dispatchers.Default) {
            try {
                // Extract text from input
                val text = when (input) {
                    is UserInput.Text -> input.text
                    is UserInput.Voice -> input.audioData
                    is UserInput.Image -> "Image input received"
                }
                
                // Check memories for context
                val memories = memoryManager.getRelevantMemories(text)
                val memoryContext = if (memories.isNotEmpty()) {
                    "\n\nRelevant memories:\n" + memories.joinToString("\n") { "- ${it.key}: ${it.value}" }
                } else ""
                
                // Get system prompt
                val systemPrompt = systemPromptManager.getSystemPrompt()
                
                // Process with AI (simulated response for now)
                val response = processWithAI(systemPrompt + memoryContext, text)
                
                // Store in memory if important
                memoryManager.processAndStore(text, response)
                
                // Save conversation
                saveConversation(text, response)
                
                LillyResponse.Success(
                    text = response,
                    conversationId = null
                )
            } catch (e: Exception) {
                LillyResponse.Error(
                    message = e.message ?: "Unknown error",
                    code = ErrorCode.PROCESSING_ERROR
                )
            }
        }
    }
    
    private suspend fun processWithAI(systemPrompt: String, input: String): String {
        // TODO: Integrate with actual AI API (Claude, OpenAI, etc.)
        // For now, return a simulated response
        return when {
            input.lowercase().contains("hello") -> "Hello! I'm Lilly, your AI assistant. How can I help you today?"
            input.lowercase().contains("help") -> "I can help you with:\n- Answering questions\n- Managing tasks\n- Controlling cursor\n- Running skills\n- Remembering information"
            input.lowercase().contains("skill") -> "I have ${skillManager.getActiveSkillsCount()} active skills. You can manage them in the Skills section."
            input.lowercase().contains("memory") -> "I remember ${memoryManager.getMemoryCount()} things. You can view and manage my memory in the Memory section."
            else -> "I understand you're asking about: $input. Let me help you with that. As your AI assistant, I'm here to provide information and assistance with any task you need."
        }
    }
    
    private suspend fun saveConversation(userMessage: String, lillyResponse: String) {
        val conversation = Conversation(
            id = java.util.UUID.randomUUID().toString(),
            messages = listOf(
                Message(
                    id = java.util.UUID.randomUUID().toString(),
                    content = userMessage,
                    isUser = true,
                    timestamp = System.currentTimeMillis()
                ),
                Message(
                    id = java.util.UUID.randomUUID().toString(),
                    content = lillyResponse,
                    isUser = false,
                    timestamp = System.currentTimeMillis()
                )
            ),
            createdAt = System.currentTimeMillis(),
            updatedAt = System.currentTimeMillis()
        )
        database.conversationDao().insertConversation(conversation)
    }
    
    suspend fun getAllConversations(): List<Conversation> {
        return withContext(Dispatchers.Default) {
            try {
                database.conversationDao().getAllConversations()
            } catch (e: Exception) {
                emptyList()
            }
        }
    }
    
    suspend fun getConversationById(id: String): Conversation? {
        return withContext(Dispatchers.Default) {
            try {
                database.conversationDao().getConversationById(id)
            } catch (e: Exception) {
                null
            }
        }
    }
    
    suspend fun deleteConversation(id: String) {
        withContext(Dispatchers.Default) {
            try {
                val conversation = database.conversationDao().getConversationById(id)
                conversation?.let {
                    database.conversationDao().deleteConversation(it)
                }
            } catch (e: Exception) {
                // Handle error
            }
        }
    }
    
    fun shutdown() {
        scope.cancel()
    }
}

sealed class UserInput {
    data class Text(val text: String) : UserInput()
    data class Voice(val audioData: String) : UserInput()
    data class Image(val imageData: String) : UserInput()
}

sealed class LillyResponse {
    data class Success(
        val text: String,
        val conversationId: String?
    ) : LillyResponse()
    
    data class Error(
        val message: String,
        val code: ErrorCode
    ) : LillyResponse()
}

enum class ErrorCode {
    PROCESSING_ERROR,
    API_ERROR,
    NETWORK_ERROR,
    AUTHENTICATION_ERROR,
    PERMISSION_ERROR
}