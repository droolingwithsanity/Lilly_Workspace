package com.lilly.assistant.services

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Intent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.lilly.assistant.core.CursorController
import kotlinx.coroutines.*

class LillyAccessibilityService : AccessibilityService() {
    
    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    private var cursorController: CursorController? = null
    
    override fun onServiceConnected() {
        super.onServiceConnected()
        
        // Configure the service info
        serviceInfo = serviceInfo.apply {
            eventTypes = AccessibilityEvent.TYPES_ALL_MASK
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            flags = AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS or
                    AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                    AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            notificationTimeout = 100
        }
        
        // Initialize cursor controller
        cursorController = CursorController(this)
    }
    
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        event ?: return
        
        when (event.eventType) {
            AccessibilityEvent.TYPE_VIEW_CLICKED -> {
                handleViewClicked(event)
            }
            AccessibilityEvent.TYPE_VIEW_LONG_CLICKED -> {
                handleViewLongClicked(event)
            }
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                handleWindowStateChanged(event)
            }
            AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED -> {
                handleTextChanged(event)
            }
        }
    }
    
    private fun handleViewClicked(event: AccessibilityEvent) {
        // Handle click events
        val source = event.source ?: return
        val bounds = android.graphics.Rect()
        source.getBoundsInScreen(bounds)
        
        // Log or process the click
        val packageName = event.packageName?.toString() ?: return
        val className = event.className?.toString() ?: return
        
        // Could forward to cursor controller or process
    }
    
    private fun handleViewLongClicked(event: AccessibilityEvent) {
        // Handle long click events
    }
    
    private fun handleWindowStateChanged(event: AccessibilityEvent) {
        // Handle window state changes
    }
    
    private fun handleTextChanged(event: AccessibilityEvent) {
        // Handle text changes
    }
    
    override fun onInterrupt() {
        // Handle service interruption
    }
    
    override fun onDestroy() {
        scope.cancel()
        cursorController?.shutdown()
        super.onDestroy()
    }
    
    fun getCursorController(): CursorController? {
        return cursorController
    }
    
    fun findNodeByText(text: String): AccessibilityNodeInfo? {
        val rootNode = rootInActiveWindow ?: return null
        return findNodeByTextRecursive(rootNode, text)
    }
    
    private fun findNodeByTextRecursive(node: AccessibilityNodeInfo, text: String): AccessibilityNodeInfo? {
        if (node.text?.toString()?.contains(text, ignoreCase = true) == true) {
            return node
        }
        
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val result = findNodeByTextRecursive(child, text)
            if (result != null) return result
        }
        
        return null
    }
    
    fun performClick(node: AccessibilityNodeInfo): Boolean {
        return node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
    }
    
    fun performLongClick(node: AccessibilityNodeInfo): Boolean {
        return node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
    }
    
    companion object {
        private var instance: LillyAccessibilityService? = null
        
        fun getInstance(): LillyAccessibilityService? = instance
        
        fun isRunning(): Boolean = instance != null
    }
    
    init {
        instance = this
    }
}