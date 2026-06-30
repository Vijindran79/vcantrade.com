f = open("config.py", "r", encoding="utf-8")
c = f.read()
f.close()

if "HAWK_REVERSAL_EXIT" not in c:
    c = c.replace(
        "ALERT_FLASH_DURATION_MS = int",
        "HAWK_REVERSAL_EXIT_ENABLED = True\nHAWK_REVERSAL_EXIT_R = 0.3\nHAWK_PEAK_PROFIT_EXIT_PCT = 0.80\nALERT_FLASH_DURATION_MS = int",
    )
    f = open("config.py", "w", encoding="utf-8")
    f.write(c)
    f.close()
    print("reversal config added to config.py")
else:
    print("already present")