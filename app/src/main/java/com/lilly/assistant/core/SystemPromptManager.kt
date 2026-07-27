package com.lilly.assistant.core

import android.content.Context
import android.content.SharedPreferences

class SystemPromptManager(private val context: Context) {
    
    private val prefs: SharedPreferences = context.getSharedPreferences(
        PREFS_NAME, Context.MODE_PRIVATE
    )
    
    init {
        initializeDefaults()
    }
    
    private fun initializeDefaults() {
        if (!prefs.contains(KEY_SYSTEM_PROMPT)) {
            prefs.edit().putString(KEY_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT).apply()
        }
    }
    
    fun getSystemPrompt(): String {
        return prefs.getString(KEY_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT) ?: DEFAULT_SYSTEM_PROMPT
    }
    
    fun setSystemPrompt(prompt: String) {
        prefs.edit().putString(KEY_SYSTEM_PROMPT, prompt).apply()
    }
    
    fun resetToDefault() {
        prefs.edit().putString(KEY_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT).apply()
    }
    
    fun getCustomInstructions(): String {
        return prefs.getString(KEY_CUSTOM_INSTRUCTIONS, "") ?: ""
    }
    
    fun setCustomInstructions(instructions: String) {
        prefs.edit().putString(KEY_CUSTOM_INSTRUCTIONS, instructions).apply()
    }
    
    fun getPersonality(): String {
        return prefs.getString(KEY_PERSONALITY, DEFAULT_PERSONALITY) ?: DEFAULT_PERSONALITY
    }
    
    fun setPersonality(personality: String) {
        prefs.edit().putString(KEY_PERSONALITY, personality).apply()
    }
    
    companion object {
        private const val PREFS_NAME = "lilly_system_prompt"
        private const val KEY_SYSTEM_PROMPT = "system_prompt"
        private const val KEY_CUSTOM_INSTRUCTIONS = "custom_instructions"
        private const val KEY_PERSONALITY = "personality"
        
        private const val DEFAULT_PERSONALITY = "helpful, friendly, and professional"
        
        private const val DEFAULT_SYSTEM_PROMPT = """You are Lilly, an intelligent AI assistant created to help users with various tasks. You are:

- Helpful: Always try to provide useful and accurate information
- Friendly: Maintain a warm and approachable tone
- Professional: Be competent and reliable in your responses
- Adaptable: Adjust your communication style based on the user's needs
- Knowledgeable: Share your knowledge when appropriate
- Respectful: Always treat users with respect and dignity

You have access to:
- Memory system to remember important information
- Skill system to perform specific tasks
- Package manager to manage installed applications
- Cursor control for automation tasks
- Real-time processing for immediate responses

When responding:
1. Be concise but thorough
2. Use clear and simple language
3. Provide actionable advice when possible
4. Ask clarifying questions when needed
5. Acknowledge limitations honestly"""
    }
}