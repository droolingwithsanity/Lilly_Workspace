package com.lilly.assistant.receivers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.lilly.assistant.services.LillyForegroundService
import kotlinx.coroutines.*

class BootReceiver : BroadcastReceiver() {
    
    override fun onReceive(context: Context?, intent: Intent?) {
        context ?: return
        intent ?: return
        
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED -> handleBootCompleted(context)
            Intent.ACTION_QUICKBOOT_POWERON -> handleBootCompleted(context)
        }
    }
    
    private fun handleBootCompleted(context: Context) {
        // Start foreground service
        CoroutineScope(Dispatchers.IO).launch {
            LillyForegroundService.startService(context)
        }
    }
}