# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.

# Keep Kotlin metadata
-keepattributes *Annotation*
-keepattributes Signature
-keepattributes InnerClasses
-keepattributes EnclosingMethod

# Keep Room entities
-keep class com.lilly.assistant.models.** { *; }

# Keep Room DAOs
-keep class com.lilly.assistant.database.** { *; }

# Keep Services
-keep class com.lilly.assistant.services.** { *; }

# Keep Receivers
-keep class com.lilly.assistant.receivers.** { *; }

# Keep Activities
-keep class com.lilly.assistant.ui.** { *; }

# Keep Application class
-keep class com.lilly.assistant.LillyApplication { *; }

# Keep core classes
-keep class com.lilly.assistant.core.** { *; }

# Firebase
-keep class com.google.firebase.** { *; }

# Room
-keep class * extends androidx.room.RoomDatabase
-keep @androidx.room.Entity class *
-dontwarn androidx.room.paging.**

# Coroutines
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory {}
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler {}
-keepclassmembers class kotlinx.coroutines.** {
    volatile <fields>;
}

# Prevent stripping of needed classes
-keep class kotlin.Metadata { *; }
-keepclassmembers class ** {
    @kotlinx.serialization.Serializable <fields>;
}

# AndroidX
-keep class androidx.** { *; }
-keep interface androidx.** { *; }

# Material Design
-keep class com.google.android.material.** { *; }

# Retrofit (if used)
-keepattributes Signature
-keepattributes Exceptions
-keepattributes *Annotation*

# OkHttp (if used)
-dontwarn okhttp3.**
-dontwarn okio.**
-keep class okhttp3.** { *; }
-keep class okio.** { *; }

# Glide (if used)
-keep public class * implements com.bumptech.glide.module.GlideModule
-keep class * extends com.bumptech.glide.module.AppGlideModule { <init>(...); }
-keep public enum com.bumptech.glide.load.ImageHeaderParser$** {
    **[] $VALUES;
    public *;
}

# Keep custom views
-keep public class * extends android.view.View {
    public <init>(android.content.Context);
    public <init>(android.content.Context, android.util.AttributeSet);
    public <init>(android.content.Context, android.util.AttributeSet, int);
    public void set*(...);
}

# Keep Parcelable
-keepclassmembers class * implements android.os.Parcelable {
    public static final android.os.Parcelable$Creator CREATOR;
}

# Keep Serializable
-keepclassmembers class * implements java.io.Serializable {
    static final long serialVersionUID;
    private static final java.io.ObjectStreamField[] serialPersistentFields;
    !static !transient <fields>;
    private void writeObject(java.io.ObjectOutputStream);
    private void readObject(java.io.ObjectInputStream);
    java.lang.Object writeReplace();
    java.lang.Object readResolve();
}

# Keep R class
-keepclassmembers class **.R$* {
    public static <fields>;
}

# Don't warn about missing classes
-dontwarn javax.annotation.**
-dontwarn sun.misc.Unsafe
-dontwarn org.codehaus.mojo.animal_sniffer.**
-dontwarn android.support.**