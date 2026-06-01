# 🍎 VcanTrade Teacher Mode Setup for Mac - Easy Guide

## ✅ For Non-Technical Users (Parents)

This guide is **super simple**. Just copy and paste the commands your son gives you, one at a time.

---

## 📋 Step-by-Step Instructions for Your Son

### Step 1: Install VcanTrade Teacher Mode (Copy & Paste This)

Open **Terminal** on your Mac and paste this **one line**:

```
curl -fsSL https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install-teacher.sh | bash
```

Press **Enter** and wait. The installer will:
- ✅ Install Python (if needed)
- ✅ Download the bot to your Mac
- ✅ Set everything up automatically

**It will ask for your Mac password once** — this is normal. Type your password and press Enter.

---

### Step 2: Before Starting the Bot (Do This Every Time)

**Open Terminal again** and paste this **one line**:

```
ollama serve
```

Leave this Terminal window **open and running**. Don't close it.

---

### Step 3: Open a NEW Terminal Window

Click the Terminal icon in your dock or press **Cmd + Space**, type "Terminal", and press Enter.

**In this NEW window**, paste this **one line**:

```
~/VcanTrade/start.sh
```

Press Enter. The bot dashboard will open.

---

## 🎯 What You'll See

The bot will show you **trading signals with entry and exit numbers**. 

- 🟢 **Green signal** = Bot says "BUY at this price"
- 🔴 **Red signal** = Bot says "SELL at this price"

You decide when to click Buy/Sell yourself. The bot never clicks for you (Teacher Mode = you stay in control).

---

## 🚨 Important: First Time Setup Only

After **Step 1 finishes**, the first time you run **Step 3**, the bot needs to download AI models. This takes **5-10 minutes**. Just wait — don't close anything.

After that, it starts instantly.

---

## ❓ Troubleshooting

**"Command not found"** when running `start.sh`?
- Make sure you typed the `~` symbol (shift + grave accent key)

**Terminal looks stuck after Step 2?**
- That's correct! `ollama serve` keeps running in the background. Open a NEW Terminal window for Step 3.

**Bot isn't showing signals?**
- Make sure TradingView Desktop is open and you're on a chart

---

## 📞 Need Help?

If something doesn't work, take a screenshot and show your son this error. That's all he needs to help fix it.

**Your son just needs to run these commands once during setup, and once per trading day you can do it yourself.**

Good luck! 🍎📈
