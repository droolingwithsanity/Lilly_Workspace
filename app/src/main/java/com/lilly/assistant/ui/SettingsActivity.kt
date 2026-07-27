package com.lilly.assistant.ui

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.preference.PreferenceFragmentCompat
import androidx.preference.SwitchPreferenceCompat
import androidx.preference.ListPreference
import androidx.preference.SeekBarPreference
import com.lilly.assistant.R

class SettingsActivity : AppCompatActivity() {
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Settings"
        
        if (savedInstanceState == null) {
            supportFragmentManager
                .beginTransaction()
                .replace(R.id.settings_container, SettingsFragment())
                .commit()
        }
    }
    
    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
    
    class SettingsFragment : PreferenceFragmentCompat() {
        
        override fun onCreatePreferences(savedInstanceState: Bundle?, rootKey: String?) {
            setPreferencesFromResource(R.xml.preferences, rootKey)
            setupPreferences()
        }
        
        private fun setupPreferences() {
            // Voice settings
            findPreference<SwitchPreferenceCompat>("voice_enabled")?.setOnPreferenceChangeListener { _, newValue ->
                // Handle voice enable/disable
                true
            }
            
            // Cursor settings
            findPreference<SwitchPreferenceCompat>("cursor_control_enabled")?.setOnPreferenceChangeListener { _, newValue ->
                // Handle cursor control enable/disable
                true
            }
            
            // Response length
            findPreference<ListPreference>("response_length")?.setOnPreferenceChangeListener { _, newValue ->
                // Handle response length change
                true
            }
            
            // Memory settings
            findPreference<SwitchPreferenceCompat>("memory_enabled")?.setOnPreferenceChangeListener { _, newValue ->
                // Handle memory enable/disable
                true
            }
            
            // Notification settings
            findPreference<SwitchPreferenceCompat>("notifications_enabled")?.setOnPreferenceChangeListener { _, newValue ->
                // Handle notifications enable/disable
                true
            }
        }
    }
}