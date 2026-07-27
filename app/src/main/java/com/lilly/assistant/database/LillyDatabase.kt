package com.lilly.assistant.database

import android.content.Context
import androidx.room.*
import com.lilly.assistant.models.*

@Database(
    entities = [
        Conversation::class,
        Memory::class,
        InstalledSkill::class,
        Preference::class
    ],
    version = 1,
    exportSchema = false
)
@TypeConverters(Converters::class)
abstract class LillyDatabase : RoomDatabase() {
    
    abstract fun conversationDao(): ConversationDao
    abstract fun memoryDao(): MemoryDao
    abstract fun skillDao(): SkillDao
    abstract fun preferenceDao(): PreferenceDao
    
    companion object {
        @Volatile
        private var INSTANCE: LillyDatabase? = null
        
        fun getInstance(context: Context): LillyDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    LillyDatabase::class.java,
                    "lilly_database"
                ).build()
                INSTANCE = instance
                instance
            }
        }
    }
}

class Converters {
    @TypeConverter
    fun fromMessages(messages: List<Message>): String {
        return messages.joinToString("|||") { "${it.id}|${it.content}|${it.isUser}|${it.timestamp}" }
    }
    
    @TypeConverter
    fun toMessages(data: String): List<Message> {
        if (data.isEmpty()) return emptyList()
        return data.split("|||").map { messageStr ->
            val parts = messageStr.split("|")
            Message(
                id = parts[0],
                content = parts[1],
                isUser = parts[2].toBoolean(),
                timestamp = parts[3].toLong()
            )
        }
    }
}