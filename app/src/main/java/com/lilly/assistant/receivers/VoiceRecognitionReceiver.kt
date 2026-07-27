package com.lilly.assistant.receivers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.lilly.assistant.LillyApplication
import kotlinx.coroutines.*

class VoiceRecognitionReceiver : BroadcastReceiver() {
    
    override fun onReceive(context: Context?, intent: Intent?) {
        context ?: return
        intent ?: return
        
        when (intent.action) {
            ACTION_VOICE_RECOGNITION -> handleVoiceRecognition(context, intent)
        }
    }
    
    private fun handleVoiceRecognition(context: Context, intent: Intent) {
        val audioData = intent.getStringExtra(EXTRA_AUDIO_DATA) ?: return
        val confidence = intent.getFloatExtra(EXTRA_CONFIDENCE, 0f)
        
        CoroutineScope(Dispatchers.IO).launch {
            val app = context.applicationContext as? LillyApplication ?: return@launch
            
            // Process voice input
            val response = app.core.processInput(com.lilly.assistant.models.UserInput.Voice(audioData))
            
            // Send response back
            val responseIntent = Intent(ACTION_VOICE_RESPONSE).apply {
                putExtra(EXTRA_RESPONSE, (response as? com.lilly.assistant.models.LillyResponse.Success)?.text ?: "Error processing voice")
                putExtra(EXTRA_CONFIDENCE, confidence)
            }
            context.sendBroadcast(responseIntent)
        }
    }
    
    companion object {
        const val ACTION_VOICE_RECOGNITION = "com.lilly.assistant.VOICE_RECOGNITION"
        const val ACTION_VOICE_RESPONSE = "com.lilly.assistant.VOICE_RESPONSE"
        const val EXTRA_AUDIO_DATA = "audio_data"
        const val EXTRA_RESPONSE = "response"
        const val EXTRA_CONFIDENCE = "confidence"
    }
}