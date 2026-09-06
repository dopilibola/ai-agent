"""Uzbek and English for the panel's own copy.

Django's own strings — the login form, the model-admin pages, "Save", "Add" —
switch by themselves: `LocaleMiddleware` plus the catalogues Django already
ships for `uz` and `en`. This module covers the panel's own pages, and it is a
plain dictionary rather than gettext for one practical reason: **the server has
no gettext**, so a `.po` could not be compiled there, and adding a build tool to
the deploy for two languages and a couple of hundred phrases is a worse trade
than a table anyone can read and edit.

Uzbek is the source language: the key *is* the Uzbek string, so a phrase with no
entry here still renders correctly in Uzbek and merely stays Uzbek in English —
a missing translation is a small blemish, never a blank page.

Templates use `{% t "…" %}` for literals and `|t` for values that come from
Python (a category label, a scheduled action's name). See
`crm/templatetags/panel_extras.py`.
"""

from __future__ import annotations

from django.utils.translation import get_language

# uz → en. Keep sorted by where it appears; keep the Uzbek exactly as written in
# the template, including the ‘ and ’ apostrophes, or the lookup misses.
EN: dict[str, str] = {
    # ---- navigation, shell
    "Bosh sahifa": "Home",
    "Mijozlar": "Customers",
    "Avto-xabarlar": "Auto messages",
    "Bot qoidalari": "Bot rules",
    "Tahlil": "Analytics",
    "Holat": "Status",
    "Sozlamalar": "Settings",
    "Jadvallar": "Tables",
    "Parol": "Password",
    "Chiqish": "Log out",
    "Bo‘limlar": "Sections",
    "Bo‘lim": "Section",
    "barcha bo‘limlar": "all sections",

    # ---- home
    "Bugun kim bilan gaplashish kerakligi — eng yuqoridan boshlab.":
        "Who to talk to today — the most likely first.",
    "Bugun gaplashish kerak": "Talk to these today",
    "Kim": "Who",
    "Ehtimol": "Chance",
    "Holati": "Where they stand",
    "Oxirgi faollik": "Last active",
    "Kategoriya": "Category",
    "Hozircha faol mijoz yo‘q.": "No active customers yet.",
    "Barcha mijozlar": "All customers",
    "Kategoriyalar": "Categories",
    "Voronka": "Funnel",
    "— faol murojaatlar": "— open leads",
    "Xabarlar": "Messages",
    "To‘langan buyurtmalar": "Paid orders",
    "Jami": "Total",
    "to‘liq tahlil": "full analytics",
    "Boshqarish": "Manage",
    "Tizim holati": "System status",
    "murojaat ochilmagan": "no lead opened",
    "javob bergan": "answered us",
    "operator": "operator",
    "kun": "days",

    # ---- customers
    "Sotib olish ehtimoli bo‘yicha saralangan. Ismga bosing — suhbati ochiladi.":
        "Sorted by how likely they are to buy. Click a name to open the conversation.",
    "Ism, @username, telefon yoki id": "Name, @username, phone or id",
    "Qidirish": "Search",
    "Saralash": "Sort",
    "Hammasi": "All",
    "Operator qo‘lida": "With an operator",
    "Odam javob berayotgan chatlar": "Chats a human is handling",
    "ta mijoz": "customers",
    "Sotib olish ehtimoli": "Chance of buying",
    "Yozishma": "Messages",
    "Hech narsa topilmadi.": "Nothing found.",
    "oldingi": "previous",
    "keyingi": "next",
    "«Yozishma» — mijoz yozgan / unga yuborilgan xabarlar soni.":
        "“Messages” — what they sent / what was sent to them.",
    "buyurtma": "orders",
    "-bosqich": " — stage",
    "sotib olish ehtimoli": "chance of buying",
    "oxirgi faollik": "last active",
    "kelgan xabarlar": "messages received",
    "yuborilgan xabarlar": "messages sent",
    "tushum": "revenue",
    "birinchi kelgan": "first seen",
    "token": "tokens",

    # ---- one customer
    "ro‘yxatga": "back to the list",
    "operator qo‘lida": "with an operator",
    "da ochish": "\u2009— open",
    "Telegramda ochish": "Open in Telegram",
    "Havolani nusxalash uchun bosing": "Click to copy the link",
    "Belgini olib tashlash": "Remove the mark",
    "✓ Sotib olgan": "✓ Mark as bought",
    "AI ni yoqish": "Turn the AI back on",
    "Keyin yozish:": "Write again in:",
    "Qachon": "When",
    "nima haqida (ixtiyoriy)": "what about (optional)",
    "Rejalashtirish": "Schedule",
    "1 hafta": "1 week",
    "1 oy": "1 month",
    "3 oy": "3 months",
    "dan keyin": " from now",
    "Suhbat": "Conversation",
    "O‘zingiz javob yozing…": "Write your own reply…",
    "Yuborish": "Send",
    "Yuborilgach AI bu chatda jim bo‘ladi.": "Once sent, the AI goes quiet in this chat.",
    "WhatsApp: mijoz oxirgi marta 24 soatdan oldin yozgan, shuning uchun erkin matn yuborib bo‘lmaydi. Mijoz o‘zi yozsa, oyna qaytadan ochiladi.":
        "WhatsApp: this customer last wrote more than 24 hours ago, so a free-text "
        "reply cannot be delivered. The window reopens as soon as they write again.",
    "Xabar matni bo‘sh.": "The message is empty.",
    "Xabarni navbatga qo‘yib bo‘lmadi.": "Could not queue the message.",
    "Xabar navbatga qo‘yildi — bir daqiqa ichida yuboriladi. AI bu chatda jim bo‘ladi.":
        "Queued — it goes out within a minute. The AI will stay quiet in this chat.",
    "Operator javobi": "Operator reply",
    "Bu chatda hali xabar yo‘q.": "No messages in this chat yet.",
    "To‘liq yozuv (vositalar bilan)": "Full log (with tool calls)",
    "Qisqacha": "Summary",
    "Mijoz yozgan": "They sent",
    "Unga yuborilgan": "Sent to them",
    "— AI javobi": "— AI replies",
    "— avto-xabar": "— auto messages",
    "— operator": "— operator",
    "Buyurtma": "Orders",
    "Tushum": "Revenue",
    "Birinchi kelgan": "First seen",
    "Bosqich": "Stage",
    "Qabr": "Grave",
    "to‘langan": "paid",
    "Rejadagi xabarlar": "Scheduled messages",
    "Rejada xabar yo‘q.": "Nothing scheduled.",
    "Bu mijozga yozilmasin": "Never write to them first",
    "bekor": "cancel",
    "kutmoqda": "waiting",
    "(operatorga)": "(to staff)",
    "Buyurtma va to‘lovlar": "Orders and payments",
    "to‘lov": "payment",
    "«Sotib olgan» kategoriyani o‘zgartiradi va rejadagi avto-xabarlarni bekor qiladi; tushum hisobiga qo‘shilmaydi — u faqat haqiqiy to‘lovlardan yig‘iladi. Rejalashtirilgan xabar matnini AI o‘sha payt mijozning tilida yozadi va faqat ish soatlarida yuboradi.":
        "“Mark as bought” changes the category and cancels their queued messages; "
        "it is not added to revenue, which only counts real payments. A scheduled "
        "message is written by the AI at send time, in the customer's language, "
        "and only within working hours.",
    "O‘zi yozsa, AI baribir javob beradi — faqat biz birinchi bo‘lib yozmaymiz.":
        "If they write, the AI still answers — we just never write first.",
    "oxirgi": "last",
    "tasi, jami": "of",

    # ---- auto messages
    "Bot mijozga o‘zi yozadimi, qachon yozadi va nima yozgan.":
        "Whether the bot writes first, when it may, and what it actually sent.",
    "Bot o‘zi birinchi yozadi.": "The bot writes first.",
    "Ta’qib xabarlari yoqilgan —": "Follow-up messages are on —",
    "orasida, bir mijozga kuniga": "only, and at most",
    "tagacha.": "per customer per day.",
    "Bot hech qachon birinchi yozmaydi.": "The bot never writes first.",
    "Mijoz yozganda odatdagidek javob beradi; operator mijoz sahifasida rejalashtirgan xabar esa baribir yuboriladi.":
        "It still answers whenever a customer writes, and a message an operator "
        "scheduled on the customer's page still goes out.",
    "Navbatda": "Queued",
    "eng yaqini": "next one",
    "rejada yo‘q": "nothing scheduled",
    "Yuborilgan": "Sent",
    "oxirgi 7 kunda": "in the last 7 days",
    "Keyinga surilgan": "Postponed",
    "qoida bo‘yicha kutmoqda": "waiting on a rule",
    "Xato": "Failed",
    "yuborib bo‘lmagan": "could not be sent",
    "Navbatdagi xabarlar": "Queued messages",
    "Qachon": "When",
    "Kimga": "To whom",
    "Nima": "What",
    "surilgan": "postponed",
    "operatorga": "to staff",
    "operator rejalashtirgan": "scheduled by an operator",
    "hozir": "now",
    "+1 kun": "+1 day",
    "Navbatda xabar yo‘q.": "Nothing queued.",
    "Qoidalar": "Rules",
    "Bot o‘zi birinchi yozsin": "Let the bot write first",
    "O‘chiq bo‘lsa bot hech qachon birinchi yozmaydi — na eslatma, na ta’qib.":
        "When off, the bot never writes first — no reminders, no chasing.",
    "Faqat shu soatlarda": "Only between",
    "Tashqarida qolgani ertalabki soatga suriladi, o‘chib ketmaydi. Bu operator rejalashtirgan xabarga ham tegishli.":
        "Anything falling outside is moved to the morning, not dropped. This "
        "applies to operator-scheduled messages too.",
    "Bir mijozga kuniga ko‘pi bilan": "At most, per customer per day",
    "ta": "",
    "Xabarlar orasida kamida": "At least, between two messages",
    "daqiqa": "minutes",
    "Bu ikki chegara faqat botning o‘z ta’qib xabarlariga tegishli.":
        "These two limits apply only to the bot's own follow-ups.",
    "Saqlash": "Save",
    "O‘zgarish bir necha soniyada ishlayotgan botga yetadi.":
        "The change reaches the running bot within seconds.",
    "Xabar matni qayerda?": "Where is the message text?",
    "Tayyor shablon yo‘q: har bir xabarni AI yuborish paytida mijozning o‘z tilida yozadi. Qanday chiqqanini pastdagi ro‘yxatdan o‘qing. Bitta mijozga xabar rejalashtirish uchun uning sahifasini oching.":
        "There is no template: each message is written by the AI at send time in "
        "the customer's own language. Read what went out below. To schedule a "
        "message for one customer, open their page.",
    "Yaqinda yuborilganlar": "Recently sent",
    "Hali avto-xabar yuborilmagan.": "No automatic message has been sent yet.",

    # ---- bot rules
    "Bot nima deyishini shu matn belgilaydi. Saqlagan zahoti ishlaydi — qayta ishga tushirish shart emas.":
        "This text decides what the bot says. A save takes effect immediately — "
        "no restart needed.",
    "Bu bo‘lim uchun tahrirlanadigan qoida yo‘q.": "Nothing editable for this section.",
    "Fayl serverda topilmadi:": "File not found on the server:",
    "Eski nusxa ko‘rsatilmoqda. «Saqlash» bosilsa aynan shu matn tiklanadi.":
        "You are looking at an older version. Pressing Save restores exactly this text.",
    "bekor qilish": "cancel",
    "Keyingi javobdanoq kuchga kiradi.": "It applies from the very next reply.",
    "Ehtiyot bo‘ling": "Take care",
    "Narxlar bu yerda emas, katalogda — matnga narx yozmang.":
        "Prices live in the catalogue, not here — never write a price into this text.",
    "bu belgilarni o‘chirmang, ular har javobda joriy vaqtga almashtiriladi.":
        "leave these placeholders in; they are replaced with the current time on every reply.",
    "Oldingi nusxalar": "Earlier versions",
    "Hali saqlangan nusxa yo‘q.": "Nothing saved yet.",
    "Har saqlashda oldingi matn shu yerga tushadi (oxirgi 10 tasi).":
        "Each save keeps the previous text here (the last 10).",
    "ko‘rish": "view",
    "Fayl": "File",

    # ---- status
    "Bot ishlayaptimi, kimdir javobsiz qolganmi, navbat qanday.":
        "Is the bot running, is anyone waiting for an answer, how big is the queue.",
    "Diqqat": "Attention",
    "Hammasi joyida.": "All good.",
    "Xizmatlar ishlayapti, baza javob beryapti, javobsiz qolgan mijoz yo‘q.":
        "Services are up, the database answers, nobody is waiting on a reply.",
    "Xizmatlar": "Services",
    "Xizmat": "Service",
    "Qachondan beri": "Since",
    "Qayta ishga tushgan": "Restarts",
    "Qayta ishga tushirish": "Restart",
    "o‘rnatilmagan": "not installed",
    "o‘chirilgan": "switched off",
    "ishlayapti": "running",
    "Qayta ishga tushirish suhbatlarni yo‘qotmaydi — tarix bazada saqlanadi. Bir necha soniya davomida bot javob bermay turadi.":
        "Restarting loses no conversations — the history lives in the database. "
        "The bot is silent for a few seconds.",
    "Ish belgilari": "Signs of life",
    "Oxirgi mijoz xabari": "Last customer message",
    "Oxirgi AI javobi": "Last AI reply",
    "Oxirgi avto-xabar": "Last automatic message",
    "Oxirgi operator xabari": "Last operator message",
    "Sutkada yozilgan hodisa": "Events logged in 24h",
    "Haftada yozilgan hodisa": "Events logged in 7 days",
    "Bazaga ulanib bo‘lmadi:": "Could not reach the database:",
    "Rejadagi xabarlar navbati": "Scheduled message queue",
    "Bajarilmoqda": "Running",
    "Xato bilan tugagan": "Failed",
    "Avto-xabarlar sahifasi": "Auto messages page",

    # ---- settings
    "Bot ishga tushganda o‘qiydigan qiymatlar. Saqlagandan keyin botni qayta ishga tushiring.":
        "Values read when the bot starts. Restart the bot after saving.",
    "Umumiy": "General",
    "Muhim.": "Important.",
    "Bu qiymatlar bot ishga tushganda o‘qiladi — saqlaganingizdan keyin kuchga kirishi uchun botni qayta ishga tushiring.":
        "These are read at start-up — restart the bot for them to take effect.",
    "Bu yerda nima yo‘q": "What is not here",
    "Payme/Uzum kalitlari, Telegram tokenlari va baza manzili ataylab ko‘rsatilmaydi va tahrirlanmaydi — ular sir, va sirning brauzerdagi maydonda turishi xavfsiz emas. Ular serverda":
        "Payme/Uzum keys, Telegram tokens and the database URL are deliberately "
        "neither shown nor editable — they are secrets, and a secret sitting in a "
        "browser field is not safe. They stay on the server in",
    "faylida qoladi.": ".",
    "Har saqlashda faylning eski nusxasi": "Every save copies the previous file to",
    "ga yoziladi.": ".",

    # ---- analytics
    "Bir davr ichida nima bo‘lgani — bitta mijoz emas, umumiy manzara.":
        "What happened over a period — the whole picture, not one customer.",
    "Kelgan xabar": "Messages received",
    "AI javobi": "AI replies",
    "Avto-xabar": "Auto messages",
    "O‘rtacha yozgan": "Average sent by a customer",
    "mijozlardan,": "from customers,",
    "1 xabarga": "per message,",
    "javob": "replies",
    "so‘ralmagan xabarlar": "messages nobody asked for",
    "javob bergan chatlar bo‘yicha": "across chats that replied",
    "to‘langan buyurtma": "paid orders",
    "Xabarlar oqimi": "Message volume",
    "Avto-xabar turlari": "Kinds of automatic message",
    "Eng faol yozganlar": "Wrote the most",
    "Eng ko‘p to‘laganlar": "Paid the most",
    "Vositalar ishlashi": "Tool reliability",
    "Vosita": "Tool",
    "Chaqiruv": "Calls",
    "Xato ulushi": "Failure rate",
    "Bu davrda vosita chaqiruvi yo‘q.": "No tool calls in this period.",
    "mijoz yozgan": "customer wrote",
    "avto-xabar": "auto message",

    # ---- transcript
    "To‘liq yozuv": "Full log",
    "ta hodisa": "events",
    "Jadval ko‘rinishi": "Table view",
    "Bu suhbat bo‘yicha hodisa topilmadi.": "No events found for this conversation.",

    # ---- eval note
    "ta test chati hisobga olinmadi": "test chats were left out",
    "ko‘rsatish": "show them",
    "Test chatlari ham hisobga olinmoqda —": "Test chats are being counted —",
    "yashirish": "hide them",

    # ---- roles
    "mijoz": "customer",
    "AI": "AI",
    "vosita": "tool",
    "holat": "outcome",
    # ---- the score's categories (crm/scoring.py::BUCKETS)
    "Juda yaqin": "Very close",
    "Yaqin": "Close",
    "O‘ylayapti": "Thinking about it",
    "Uzoq": "Far off",
    "To‘lovi o‘tgan — endi xizmat ko‘rsatiladi": "Paid — now it is service work",
    "Narx aytilgan, javob beryapti — bugun gaplashing":
        "Quoted and answering — talk to them today",
    "Qiziqyapti, hali qaror qilmagan": "Interested, not decided",
    "Boshlagan, lekin sust": "Started, but quiet",
    "Deyarli yo‘q — o‘zi yozsa javob beramiz":
        "Barely there — we answer if they write",
    "Rad etgan yoki «yozmang» degan": "Said no, or asked us to stop",
    "To‘langan buyurtmasi bor": "Has a paid order",
    "Operator «sotib olgan» deb belgilagan": "An operator marked them as bought",
    "Yopilgan:": "Closed:",
    "Yozgan, murojaat ochilmagan": "Wrote, no lead opened",
    "1-bosqich: tanishuv": "Stage 1: getting acquainted",
    "2-bosqich: qabr ma’lum": "Stage 2: the grave is known",
    "3-bosqich: narx aytilgan": "Stage 3: a price was quoted",
    "4-bosqich: to‘lov havolasi berilgan": "Stage 4: payment link sent",
    "Telefon raqamini bergan": "Left a phone number",
    "Oxirgi xabarimizga javob bergan": "Answered our last message",
    "Yozdik — javob bermadi": "We wrote — no answer",
    "Buyurtma ochilgan, to‘lov kutilmoqda": "Order opened, payment pending",
    "Kuzatuv to‘xtatilgan (sovigan)": "Follow-up stopped (gone cold)",
    "Bugun yozgan": "Wrote today",
    "Faolligi noma’lum": "Activity unknown",
    "bosqich": "stage",

    # ---- manual category, flash messages
    "Faol": "Active",
    "Sotib olgan": "Bought",
    "Yopilgan": "Closed",
    "O‘zgartirish": "Change",
    "Ehtimol o‘zi hisoblanadi": "The score is computed",
    "To‘lagan yoki kelishilgan": "Paid, or agreed",
    "Rad etgan yoki keraksiz": "Said no, or gone",
    "Sotib olishga yaqin": "Close to buying",
    "Bugun yozganlar": "Wrote today",
    "Yangi (7 kun)": "New (7 days)",
    "Navbatdagi avto-xabar": "Queued auto messages",
    "40% dan yuqori mijozlar": "customers above 40%",
    "oxirgi 24 soat": "in the last 24 hours",
    "birinchi marta yozganlar": "wrote for the first time",
    "so‘m": "so‘m",
    "tasi shu hafta yuborilgan": "sent this week",
    "AI o‘chirilgan chatlar": "chats with the AI switched off",
    "Noto‘g‘ri chat_id": "Invalid chat id",
    "AI qayta yoqildi.": "the AI is back on.",
    "mute holatida emas edi.": "was not muted.",
    "Bu chatda murojaat (lid) yo‘q — avval mijoz bilan suhbat boshlanishi kerak.":
        "There is no lead for this chat yet — a conversation has to start first.",
    "«Sotib olgan» deb belgilandi. Rejadagi avto-xabarlar bekor qilindi. Tushum hisobiga qo‘shilmaydi — u faqat haqiqiy to‘lovlardan yig‘iladi.":
        "Marked as bought. Queued automatic messages were cancelled. It is not "
        "added to revenue, which only counts real payments.",
    "Yopilgan deb belgilandi, rejadagi xabarlar bekor qilindi.":
        "Marked as closed; queued messages were cancelled.",
    "Faol holatga qaytarildi.": "Back to active.",
    "{n} kundan keyin «bu oy ham parvarish qilaymizmi?» degan taklif avtomatik yuboriladi — kerak bo‘lmasa pastdan bekor qiling.":
        "In {n} days an offer — “shall we look after it this month too?” — goes "
        "out automatically; cancel it below if it is not wanted.",
    "Bir oydan keyin: takroriy parvarish taklifi":
        "A month later: offer to do it again",
    "Bu bo‘lim uchun rejalashtirish qo‘llab-quvvatlanmaydi.":
        "Scheduling is not supported for this section.",
    "shu vaqtda yoziladi, matnini AI mijozning tilida yozadi.":
        "is when it will be sent; the AI writes it then, in the customer's language.",
    "Noma’lum amal.": "Unknown action.",
    "Bajarilmadi — bu xabar endi navbatda emas.":
        "Nothing done — that message is no longer queued.",
    "Xabar bekor qilindi.": "Message cancelled.",
    "Bir kunga surildi.": "Moved one day later.",
    "Navbatga qo‘yildi — ruxsat etilgan eng yaqin vaqtda ketadi.":
        "Queued — it goes out at the earliest allowed moment.",
    "Noto‘g‘ri vazifa raqami.": "Invalid task number.",
    "ta rejadagi xabar bekor qilindi.": "queued messages were cancelled.",
    "Bu chat uchun rejadagi xabar yo‘q edi.": "There was nothing queued for this chat.",
    "Bunday qoida fayli yo‘q.": "No such rules file.",
    "Saqlandi": "Saved",
    "Ism, @username, telefon yoki id": "Name, @username, phone or id",
}


def t(text) -> str:
    """Translate one phrase for the active language.

    Uzbek is the source, so `uz` (and anything unknown) returns the input
    unchanged; only `en` consults the table, and a missing entry falls through
    to the Uzbek rather than to an empty string.
    """
    if text is None:
        return ""
    text = str(text)
    if (get_language() or "uz").split("-")[0] != "en":
        return text
    return EN.get(text, text)
