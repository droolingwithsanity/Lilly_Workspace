package com.lilly.assistant.models

import androidx.room.*

@Entity(tableName = "installed_skills")
data class InstalledSkill(
    @PrimaryKey
    val id: String,
    val name: String,
    val packageName: String,
    val description: String,
    val version: String,
    val isEnabled: Boolean,
    val installedAt: Long
)