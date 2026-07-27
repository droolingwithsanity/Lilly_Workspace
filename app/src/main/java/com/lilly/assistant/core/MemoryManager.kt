package com.lilly.assistant.core

import com.lilly.assistant.database.MemoryDao
import com.lilly.assistant.models.Memory
import kotlinx.coroutines.*

class MemoryManager(private val memoryDao: MemoryDao) {
    
    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    
    suspend fun getRelevantMemories(query: String): List<Memory> {
        return withContext(Dispatchers.Default) {
            try {
                // Simple keyword matching for relevance
                val keywords = query.lowercase().split(" ", ",", ".", "!", "?")
                    .filter { it.length > 2 }
                
                val allMemories = memoryDao.getAllMemories()
                
                allMemories.filter { memory ->
                    val memoryText = "${memory.key} ${memory.value}".lowercase()
                    keywords.any { keyword -> memoryText.contains(keyword) }
                }.take(10) // Limit to top 10 relevant memories
            } catch (e: Exception) {
                emptyList()
            }
        }
    }
    
    suspend fun processAndStore(input: String, response: String) {
        withContext(Dispatchers.Default) {
            try {
                // Check if input contains information worth remembering
                val shouldRemember = shouldRemember(input)
                
                if (shouldRemember) {
                    val key = extractKey(input)
                    val value = extractValue(input, response)
                    
                    if (key.isNotEmpty() && value.isNotEmpty()) {
                        val memory = Memory(
                            id = java.util.UUID.randomUUID().toString(),
                            key = key,
                            value = value,
                            timestamp = System.currentTimeMillis()
                        )
                        memoryDao.insertMemory(memory)
                    }
                }
            } catch (e: Exception) {
                // Silently fail for memory storage
            }
        }
    }
    
    private fun shouldRemember(input: String): Boolean {
        val rememberKeywords = listOf(
            "remember", "note", "important", "don't forget",
            "my name is", "i am", "i work", "i live",
            "preference", "like", "dislike"
        )
        
        return rememberKeywords.any { keyword ->
            input.lowercase().contains(keyword)
        }
    }
    
    private fun extractKey(input: String): String {
        // Extract a meaningful key from the input
        val lowerInput = input.lowercase()
        
        return when {
            lowerInput.contains("my name is") -> "user_name"
            lowerInput.contains("i work") -> "user_occupation"
            lowerInput.contains("i live") -> "user_location"
            lowerInput.contains("preference") -> "user_preference"
            lowerInput.contains("like") -> "user_like"
            lowerInput.contains("dislike") -> "user_dislike"
            else -> "note_${System.currentTimeMillis()}"
        }
    }
    
    private fun extractValue(input: String, response: String): String {
        // Extract the value to remember
        return when {
            input.lowercase().contains("my name is") -> {
                input.substringAfter("my name is").trim().split(" ").first()
            }
            input.lowercase().contains("i work") -> {
                input.substringAfter("i work").trim().split(" ").take(3).joinToString(" ")
            }
            input.lowercase().contains("i live") -> {
                input.substringAfter("i live").trim().split(" ").take(3).joinToString(" ")
            }
            else -> input.take(100) // Limit length
        }
    }
    
    suspend fun getMemoryCount(): Int {
        return withContext(Dispatchers.Default) {
            try {
                memoryDao.getAllMemories().size
            } catch (e: Exception) {
                0
            }
        }
    }
    
    fun shutdown() {
        scope.cancel()
    }
}