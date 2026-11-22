# Meta App Review — ariza matnlari

App ID `1510406410843186` · Business portfolio `maskan` (4338965196317957)
Panel: https://teamworks.uz · Privacy: /privacy · Terms: /terms · Deletion: /data-deletion

Meta arizani **faqat inglizcha** qabul qiladi. Har bandda avval inglizcha matn
(nusxalab qo'yiladigan), keyin o'zbekcha izohi turibdi.

---

## 0. App description (Basic settings / App Review kirish qismi)

> Maskan is a grave-care service in Tashkent, Uzbekistan. Families order cleaning,
> watering, painting and seasonal care for a relative's grave. Our app is the
> messaging layer of that service: a customer writes to the business on WhatsApp,
> Messenger or Instagram Direct, an assistant answers in Uzbek or Russian with the
> real price list, confirms which cemetery and which grave, opens an order, and
> sends the payment link. A human operator at Maskan watches every conversation in
> our own admin panel and can take over and type at any moment. We do not send
> unsolicited messages: the business only replies to people who wrote first, plus
> transactional order updates the customer asked for.

**O'zbekcha:** ilova nima qilishini tushuntiradi — mijoz o'zi yozadi, bot javob
beradi, operator istalgan payt aralasha oladi, hech kimga birinchi bo'lib yozmaydi.

---

## 1. whatsapp_business_messaging

> Our business receives customer enquiries on its WhatsApp number. This permission
> lets the app read the inbound message via the webhook and send the reply back
> through the Cloud API. Without it the conversation cannot happen at all: a
> customer asks "how much is cleaning a grave in Chigatoy cemetery", the app looks
> the price up in our own catalogue and answers with the exact figure, then sends
> the payment link when the customer agrees.
>
> All replies are inside the 24-hour customer service window. Outside that window
> we only send templates the customer opted into (an order status update and a
> payment reminder), never marketing.
>
> Reviewer steps: send any message to the test number; the assistant replies within
> a few seconds with the service list and prices.

**O'zbekcha:** WhatsApp'da javob yozish uchun. 24 soatlik oyna ichida oddiy javob,
tashqarisida faqat mijoz roziligi bilan shablon.

---

## 2. whatsapp_business_management

> We manage our own WhatsApp Business Account programmatically: registering the
> phone number, keeping the webhook subscription alive, and reading the status of
> the message templates we submitted (order confirmation, payment reminder,
> seasonal care offer). The app also reads delivery statuses so an operator sees in
> the panel whether a reply actually reached the customer.
>
> We do not manage any WABA other than our own.

**O'zbekcha:** o'z WABA'mizni boshqarish — raqam, webhook, shablon holati, yetkazib
berish statuslari.

---

## 3. pages_messaging

> Customers also write to the business Facebook Page. This permission lets the app
> receive those messages and reply in the same conversation, with the same
> assistant and the same price list as on WhatsApp. Replies are sent inside the
> standard messaging window; the human_agent tag is used only when a Maskan
> operator answers personally from our panel.
>
> Reviewer steps: message the Page; the assistant replies with the service list.

**O'zbekcha:** Facebook sahifaga kelgan xabarlarga javob yozish uchun.

---

## 4. pages_manage_metadata

> The app subscribes the Page to the messages webhook and keeps that subscription
> in place. That is the only thing this permission is used for — we do not edit the
> Page's settings, posts or profile.

**O'zbekcha:** sahifani webhook'ga ulash uchun, boshqa hech narsaga emas.

---

## 5. pages_show_list / pages_read_engagement

> During setup the business owner signs in and picks which of their Pages the
> assistant should answer for; pages_show_list is what populates that picker.
> pages_read_engagement is used to read the Page name and picture so the operator
> panel shows which Page a conversation belongs to when more than one is connected.

**O'zbekcha:** sozlash paytida qaysi sahifa ekanini tanlash va panelda ko'rsatish uchun.

---

## 6. instagram_business_basic

> The business has an Instagram professional account. This permission reads the
> account's own profile (id, username, picture) so the operator panel can label the
> conversation and so the app knows which account a webhook event belongs to. We do
> not read anyone else's profile or media.

**O'zbekcha:** o'z Instagram akkauntimiz profilini o'qish — suhbat qaysi akkauntga
tegishli ekanini bilish uchun.

---

## 7. instagram_business_manage_messages

> Customers send Direct messages to the business Instagram account asking the same
> questions they ask on WhatsApp. This permission lets the app read those messages
> and reply in the thread. Only conversations that a customer started are read, and
> only within the reply window Instagram allows. A Maskan operator can take over the
> thread from our panel at any time.
>
> Reviewer steps: send a Direct message to the business account; the assistant
> replies with the service list and prices.

**O'zbekcha:** Instagram Direct'da javob yozish uchun.

---

## 8. business_management

> Used only to read our own business portfolio's assets during setup — which WABA,
> which Page and which Instagram account belong to the business — so the operator
> connects the right ones. We never write to another business's portfolio.

**O'zbekcha:** sozlash paytida o'z portfelimizdagi aktivlarni ko'rish uchun.

---

## Data handling (Meta so'raydigan savollar uchun tayyor javob)

> **What data do you store?** The message text, the sender's platform id, and the
> order the conversation produced. Nothing else.
> **Where?** A PostgreSQL database on our own server in the EU/US region, reachable
> only over TLS, with access limited to Maskan staff accounts on
> https://teamworks.uz.
> **How long?** Conversations are kept while the customer is an active client and
> deleted on request through https://teamworks.uz/data-deletion.
> **Do you share it?** No. It is not sold, not shared with third parties, and not
> used for advertising. The message text is sent to our language-model provider
> solely to compose the reply.

---

## Screencast — nima yozib olish kerak

Meta har bir ruxsat uchun ekran yozuvini so'raydi. Bitta 2-3 daqiqalik video
yetadi, ichida ketma-ket:

1. Mijoz WhatsApp'dan "Assalomu alaykum, qabr tozalash qancha turadi?" deb yozadi
   → bot narx ro'yxati bilan javob beradi.
2. O'sha suhbat https://teamworks.uz panelida ochiladi → operator o'zi bir jumla
   yozadi → mijoz telefonida o'sha xabar ko'rinadi.
3. Xuddi shu narsa Messenger'da takrorlanadi.
4. Xuddi shu narsa Instagram Direct'da takrorlanadi.
5. Oxirida https://teamworks.uz/privacy va /data-deletion sahifalari ko'rsatiladi.

Videoda haqiqiy telefon ekrani va panel ekrani ko'rinishi shart — Meta ekran
yozuvisiz arizani rad etadi.
