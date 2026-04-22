import os

MAP = {
    "🦁": "[LION]", "🧠": "[BRAIN]", "🚀": "[SUCCESS]", "🌐": "[GLOBE]", "😈": "[DEVIL]",
    "⚡": "[BOLT]", "🔥": "[FIRE]", "💰": "[MONEY]", "📊": "[CHART]", "🎯": "[TARGET]",
    "🛡️": "[SHIELD]", "✅": "[OK]", "❌": "[FAIL]", "⚠️": "[WARN]", "🎉": "[CELEBRATE]",
    "💀": "[SKULL]", "👁️": "[EYE]", "💎": "[GEM]", "🐍": "[SNAKE]", "🤖": "[ROBOT]",
    "⚙️": "[GEAR]", "📈": "[UP]", "📉": "[DOWN]", "🏆": "[TROPHY]", "💡": "[IDEA]",
    "🔄": "[REFRESH]", "⏳": "[WAIT]", "📝": "[NOTE]", "🔒": "[LOCK]", "🔓": "[UNLOCK]",
    "🚫": "[BLOCK]", "🛑": "[STOP]", "✨": "[SPARKLE]", "📢": "[BROADCAST]", "👿": "[ANGRY]",
    "🔴": "[RED]", "🟢": "[GREEN]", "🟡": "[YELLOW]", "🔵": "[BLUE]", "🕰️": "[CLOCK]",
    "🌙": "[MOON]", "☀️": "[SUN]", "⬆️": "[UP]", "⬇️": "[DOWN]", "🆕": "[NEW]",
    "🌀": "[SWIRL]", "🏁": "[FLAG]", "⚔️": "[SWORDS]", "🧪": "[TEST]", "🗡️": "[DAGGER]",
    "📡": "[SAT]", "🤝": "[HANDSHAKE]", "💪": "[STRONG]", "🦅": "[EAGLE]", "🐆": "[LEOPARD]",
    "🦈": "[SHARK]", "🐺": "[WOLF]", "🔭": "[SCOPE]", "🪐": "[PLANET]", "🌌": "[GALAXY]",
    "🏹": "[BOW]", "🦾": "[ARM]", "🧬": "[DNA]", "🔮": "[ORB]", "🕹️": "[JOYSTICK]",
    "🧿": "[EYE]", "🦀": "[CRAB]", "👾": "[ALIEN]", "🐉": "[DRAGON]", "🐲": "[DRAGON]",
    "🦕": "[DINO]", "🦖": "[DINO]", "🐊": "[CROC]", "🐅": "[TIGER]", "🦓": "[ZEBRA]",
    "🦍": "[APE]", "🦧": "[APE]", "🐘": "[ELEPHANT]", "🦛": "[HIPPO]", "🦏": "[RHINO]",
    "🐪": "[CAMEL]", "🐫": "[CAMEL]", "🦒": "[GIRAFFE]", "🦘": "[KANGAROO]", "🐃": "[BUFFALO]",
    "🐂": "[BULL]", "🐄": "[COW]", "🐎": "[HORSE]", "🐖": "[PIG]", "🐏": "[RAM]",
    "🐑": "[SHEEP]", "🦙": "[LLAMA]", "🐐": "[GOAT]", "🦌": "[DEER]", "🐕": "[DOG]",
    "🐩": "[POODLE]", "🦮": "[DOG]", "🐕‍🦺": "[DOG]", "🐈": "[CAT]", "🐈‍⬛": "[CAT]",
    "🐓": "[ROOSTER]", "🦃": "[TURKEY]", "🦚": "[PEACOCK]", "🦜": "[PARROT]", "🦢": "[SWAN]",
    "🦩": "[FLAMINGO]", "🕊️": "[DOVE]", "🐇": "[RABBIT]", "🦝": "[RACCOON]", "🦨": "[SKUNK]",
    "🦡": "[BADGER]", "🦦": "[OTTER]", "🦥": "[SLOTH]", "🐁": "[MOUSE]", "🐀": "[RAT]",
    "🐿️": "[CHIPMUNK]", "🦔": "[HEDGEHOG]", "🐾": "[PAWS]", "☁️": "[CLOUD]", "🕐": "[CLOCK]",
    "💧": "[DROP]", "⏰": "[ALARM]", "🌴": "[PALM]", "📋": "[CLIPBOARD]", "₿": "[BTC]",
    "▶️": "[PLAY]", "🚶": "[WALK]", "📏": "[RULER]", "🏛️": "[GOVERN]", "📰": "[NEWS]",
    "📦": "[BOX]", "📌": "[PIN]", "🪟": "[WINDOW]", "🖱️": "[MOUSE]", "⛔": "[NO_ENTRY]",
    "🧭": "[COMPASS]", "💤": "[SLEEP]", "📸": "[CAMERA]", "🔍": "[MAGNIFY]", "⏭️": "[SKIP]",
    "✓": "[OK]", "✗": "[FAIL]", "⟲": "[RELOAD]", "●": "[DOT]", "🎓": "[GRADUATE]",
    "💵": "[CASH]", "🚨": "[SIREN]", "🟩": "[GREEN_SQ]", "⚪": "[WHITE]", "🧹": "[BROOM]",
    "🟥": "[RED_SQ]", "⏸": "[PAUSE]",
}

root = r"c:\Users\vijin\vcantrade.com-3"
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".pytest_cache", ".ruff_cache", ".qwen")]
    for fname in filenames:
        if fname.endswith(".py") and fname != "emoji_purge.py":
            fpath = os.path.join(dirpath, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            new_content = content
            for emoji, tag in MAP.items():
                new_content = new_content.replace(emoji, tag)
            if new_content != content:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                print(f"[FIXED] {fpath}")
