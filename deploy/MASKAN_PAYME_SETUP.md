# Maskan bot — Payme (va Uzum) to'lovini ulash

Bot mijozni buyurtmagacha olib boradi, lekin `create_order` to'lov havolasini faqat
merchant kaliti sozlanganda beradi. Aks holda u ataylab havola **o'ylab topmaydi** —
`call_human` chaqiradi. Quyida o'sha kalitni ulashning to'liq tartibi.

## 0. Nega alohida kassa kerak

Maskan'ning mavjud Payme kassasi jonli (`PAYME_TEST_MODE=0`) va uning callback
manzili allaqachon mobil ilovaning backendiga ro'yxatdan o'tgan:

    https://app.mas-kan.uz/api/payme/merchant/      (jazzmin, webapp/routers/mobile_v1.py)
    account maydoni: ac.order_id

Payme'da **bitta kassa = bitta callback URL**. Agar bot o'sha `merchant_id` bilan
havola yasasa, Payme to'lovni tasdiqlashda botga emas, o'sha manzilga murojaat
qiladi va `order_id` ni *ilovaning* bazasidan qidiradi. Bot invoyslari ham 1 dan
boshlanadi — ya'ni mijozning puli boshqa buyurtmaga yozilib ketishi mumkin.

Shuning uchun: **o'sha Payme merchant hisobida ikkinchi kassa**. Pul aynan o'sha
hisobga tushadi, faqat kassa va endpoint alohida bo'ladi.

## 1. Payme kabinetida (siz qilasiz)

Yangi kassa ochib, quyidagilarni sozlang:

| Sozlama | Qiymat |
|---|---|
| Endpoint (Merchant API URL) | `https://pay.mas-kan.uz/payme` |
| Account (hisob) maydoni nomi | `order_id` |
| Minimal summa | eng arzon xizmatdan past (hozir 25 000 so'm = 2 500 000 tiyin) |
| Rejim | avval test, tekshirilgach jonli |

Keyin menga bering: **merchant_id** va **Merchant API kaliti (kassa paroli)**.
Agar hisob maydonini `order_id` dan boshqa nom bilan ochsangiz — o'sha nomni ayting,
`MASKAN_PAYME_ACCOUNT_FIELD` shunga moslanadi.

## 2. DNS (siz qilasiz)

`pay.mas-kan.uz` → A yozuv → `34.61.5.37`.

Ataylab alohida subdomen: `app.mas-kan.uz` nginx bloki va Maskan prodiga tegilmaydi.

## 3. Server (men qilaman)

```bash
# .env — ai-agent papkasida
MASKAN_PAYME_MERCHANT_ID=<yangi kassa merchant_id>
MASKAN_PAYME_MERCHANT_KEY=<Merchant API kaliti>
MASKAN_PAYME_TEST_MODE=1          # sinovdan keyin 0
MASKAN_PAYME_ACCOUNT_FIELD=order_id
MASKAN_PAYMENTS_API_PORT=58230

# nginx + TLS
sudo cp deploy/maskan-payments.nginx.conf /etc/nginx/sites-available/maskan-pay
sudo sed -i 's/pay\.example\.uz/pay.mas-kan.uz/' /etc/nginx/sites-available/maskan-pay
sudo ln -s /etc/nginx/sites-available/maskan-pay /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d pay.mas-kan.uz

# webhook jarayoni
sudo systemctl enable --now ai-agent-maskan-payments
sudo systemctl restart ai-agent-maskan        # bot yangi .env ni o'qisin
```

## 4. Tekshirish

Startup logida bu **yo'qolishi** kerak:

    WARNING apps.maskan.main — No payment provider configured … orders can be created but not paid.

Protokol sinovi (soxta kalit bilan lokal portda allaqachon o'tkazilgan —
CheckPerform→Create→Perform→`paid`, idempotent, noto'g'ri summa/kalit rad etiladi,
12 soatlik muddat qoidasi ishlaydi). Jonli kassada Payme'ning o'z test kabineti
skriptini ishlatish kifoya.

Keyin botga oddiy mijoz sifatida yozib ko'rish: buyurtma tasdiqlangach javobda
`Payme: https://checkout.paycom.uz/...` havolasi chiqishi kerak — "hamkasbim
bog'lanadi" emas.

## 5. Uzum (keyinroq)

`~/maskan_mobil/jazzmin/.env` da Uzum kalitlari bor, lekin `UZUM_TEST_MODE=1` —
hali jonli emas. Uzum ham xuddi shu mantiq: o'z callback manzillari
(`/uzum/check`, `/uzum/create`, `/uzum/confirm`, `/uzum/reverse`) kerak, ya'ni bot
uchun alohida service. Jonli bo'lgach `MASKAN_UZUM_SERVICE_ID` / `MASKAN_UZUM_LOGIN`
/ `MASKAN_UZUM_PASSWORD` qo'shiladi va havola avtomatik ikkitaga aylanadi.

## Eslatma

Pulni hech qanday tool "to'landi" deb belgilay olmaydi — buni faqat provayderning
callbacki qiladi (`payments_api.py`), uni `payment_watcher.py` kuzatadi va mijozga
rahmat aytadi. Bu ataylab shunday: agent ishontirib qo'yishi mumkin, callback esa
yo'q.
