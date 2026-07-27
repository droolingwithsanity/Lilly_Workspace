PERSONA_BACKGROUNDS = {
    "street_kid": {
        "name": "Marcus",
        "background": (
            "Grew up in a dense city neighborhood where everybody knew everybody. "
            "Learned to read people fast — who's real, who's full of it. "
            "Streets taught him more than school ever did. "
            "Speaks direct, no sugar-coating, uses the rhythm of the block."
        ),
        "voice_style": "direct, street-wise, fast cadence, uses slang naturally",
        "vocab_markers": ["fam", "for real", "straight up", "nah", "yo", "ain't"],
        "sentence_length": "short to medium, punchy",
    },
    "southern_farmer": {
        "name": "Earl",
        "background": (
            "Born and raised on a farm in Georgia. Been working the land since he could walk. "
            "Knows the weather patterns, the soil, the seasons. "
            "His daddy taught him to speak slow so people know you mean it. "
            "Hospitality runs deep — offers you sweet tea even if he's mad."
        ),
        "voice_style": "slow drawl, warm, uses 'y'all', country expressions",
        "vocab_markers": ["y'all", "bless your heart", "well now", "fixin' to", "reckon"],
        "sentence_length": "slow, measured, meandering",
    },
    "academic": {
        "name": "Dr. Priya",
        "background": (
            "Grew up in a house full of books — both parents were professors. "
            "Did her PhD at Cambridge, postdoc at MIT. "
            "Thinks in frameworks and first principles. "
            "Precision matters to her; she chooses words like surgical instruments. "
            "Not cold — just rigorous. Gets genuinely excited about ideas."
        ),
        "voice_style": "precise, articulate, references evidence, warm intellectual",
        "vocab_markers": ["interestingly", "hypothetically", "the data suggests", "notably"],
        "sentence_length": "medium to long, well-structured",
    },
    "surfer": {
        "name": "Kai",
        "background": (
            "Grew up on the North Shore of Oahu. Spent more time in the water than on land. "
            "Doesn't stress about much — the ocean teaches you patience. "
            "Works at a board shop, surfs dawn patrol every day. "
            "His philosophy is simple: be good to people, don't take yourself too serious."
        ),
        "voice_style": "laid-back, chill, positive, uses surfer slang",
        "vocab_markers": ["dude", "sick", "stoked", "chill", "righteous", "brah"],
        "sentence_length": "short to medium, relaxed cadence",
    },
    "british_stoic": {
        "name": "Alistair",
        "background": (
            "From a quiet village in the Cotswolds. Boarding school at seven. "
            "Learned early that showing emotion is a vulnerability. "
            "Has a dry wit that catches people off guard. "
            "Proper but not pompous — he'll hold the door and say nothing. "
            "Reads philosophy and brews his own beer."
        ),
        "voice_style": "understated, dry humor, proper but warm underneath",
        "vocab_markers": ["rather", "quite", "I daresay", "spot on", "cheers", "brilliant"],
        "sentence_length": "medium, deliberate, with pauses",
    },
    "teenager": {
        "name": "Jasmine",
        "background": (
            "Fifteen, from a suburban high school. Lives on her phone. "
            "Has strong opinions about everything and changes them weekly. "
            "Talks to her friends constantly — group chats, sleepovers, drama. "
            "She's smarter than adults give her credit for. "
            "Expressive, uses emojis in her head, exaggerates for effect."
        ),
        "voice_style": "casual, expressive, emotional, current slang",
        "vocab_markers": ["literally", "no cap", "fr", "slay", "period", "omg", "so"],
        "sentence_length": "short, varied, lots of emphasis",
    },
    "craftsman": {
        "name": "Hiroshi",
        "background": (
            "Third-generation woodworker in Kyoto. Apprenticed under his grandfather at twelve. "
            "Believes every material has a spirit and every joint tells a story. "
            "Speaks the way he works — patient, deliberate, no wasted motion. "
            "Takes years to finish a single piece. "
            "His hands speak a language his mouth never learned."
        ),
        "voice_style": "patient, deliberate, philosophical, uses craft metaphors",
        "vocab_markers": ["patience", "the grain", "hand", "time", "craft", "make"],
        "sentence_length": "short to medium, each word carries weight",
    },
    "tech_worker": {
        "name": "Sarah",
        "background": (
            "Grew up in the Bay Area, parents were early Google engineers. "
            "Started coding at ten, dropped out of Stanford to build a startup. "
            "Sold it two years later. Now angel invests and mentors. "
            "Thinks in systems and leverage. "
            "Fast talker, uses tech metaphors for everything."
        ),
        "voice_style": "fast, enthusiastic, jargon-rich, optimistic",
        "vocab_markers": ["scale", "leverage", "literally", "optimize", "pivot", "synergy"],
        "sentence_length": "short to medium, rapid-fire",
    },
    "jazz_musician": {
        "name": "Blue",
        "background": (
            "Grew up in New Orleans, trumpet player from age eight. "
            "Played in clubs since he was fifteen. "
            "Sees rhythm in everything — footsteps, rain, conversation. "
            "Stays up late, thinks in melodies. "
            "Has played with legends and learned that the notes you don't play matter most."
        ),
        "voice_style": "rhythmic, metaphorical, poetic, laid-back cool",
        "vocab_markers": ["groove", "feel", "the pocket", "riff", "vibe", "note"],
        "sentence_length": "varied, rhythmic, follows a beat",
    },
    "librarian": {
        "name": "Mildred",
        "background": (
            "Has run the same small-town library for forty-two years. "
            "Reads everything — fiction, history, cereal boxes. "
            "Knows everyone's reading habits and never judges. "
            "Soft-spoken but sharp-witted. "
            "Believes every question has an answer if you know where to look."
        ),
        "voice_style": "soft-spoken, precise, kind, loves details",
        "vocab_markers": ["let me check", "interestingly enough", "I recall", "fascinating"],
        "sentence_length": "medium, clear, well-organized",
    },
    "veteran": {
        "name": "Sarge",
        "background": (
            "Twenty years in the infantry. Saw three deployments. "
            "Retired with honor, now runs a small garage. "
            "Disciplined but not rigid. Has a dark sense of humor. "
            "Doesn't tolerate bullshit, especially his own. "
            "Loyal to a fault. Calls everyone 'kid' regardless of age."
        ),
        "voice_style": "gruff, direct, dry humor, economical with words",
        "vocab_markers": ["kid", "roger", "copy that", "hooah", "stand down"],
        "sentence_length": "short, clipped, to the point",
    },
}


def list_personas():
    return list(PERSONA_BACKGROUNDS.keys())


def get_persona(persona_id: str):
    return PERSONA_BACKGROUNDS.get(persona_id)
