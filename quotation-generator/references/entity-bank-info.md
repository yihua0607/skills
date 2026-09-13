# 实体银行信息对照

> 本表仅供人读参考；SWIFT CODE 与银行信息以 `config/entities.json` 为准（两者不一致时以 entities.json 为准）。

## SWIFT CODE 状态

| 实体 | SWIFT CODE | 状态 |
|------|-----------|------|
| jakarta (PT. SHAN HAI MAP) | CENAIDJA (IDR) / BNINIDJAXXX (USD) / ICBKIDJAXXX (RMB) | ✅ 已配置 |
| beijing (北京山海图科技有限公司) | — | ➖ 无需配置（刘旭 2026-09-13 确认：北京不要 SWIFT CODE） |
| xian (北京山海图科技有限公司西安分公司) | BKCHCNBJ620 | ✅ 已配置 |
| shenzhen (北京山海图科技有限公司深圳分公司) | BKCHCNBJ45A | ✅ 已配置 |
| shanghai (北京山海图科技有限公司上海分公司) | BKCHCNBJ300 | ✅ 已配置 |
| shanghai_new (上海山海图新企业咨询有限公司) | BKCHCNBJ300 | ✅ 已配置 |
| singapore (SHAN HAI MAP CONSULTANCY PTE.LTD) | OCBCSGSG | ✅ 已配置 |
| deyin (PT DEIN TALENT SOLUTIONS) | CENAIDJAXXX / BNINIDJAXXX | ✅ 已配置 |
| thailand (SHAN HAI MAP (THAILAND) CO., LTD.) | KASITHBK | ✅ 已配置 |
| vietnam (CÔNG TY TNHH SHANHAIMAP VIỆT NAM) | BFTVVNVX | ✅ 已配置 |
| egypt (SHAN HAI MAP FOR CONSULTING CO) | QNBAEGCXXXX | ✅ 已配置 |
| malaysia (SHANHAIMAP SDN. BHD.) | OCBCMYKL | ✅ 已配置 |

## 完整银行信息

### jakarta
按币种使用不同账户（`bank_lines_by_currency`）：
- **IDR 账户**：
  - 银行：BCA (KCP CENTRAL PARK)
  - 户名：PT. SHAN HAI MAP
  - 账号：5485225789
  - SWIFT：CENAIDJA
  - 分行：KCP CENTRAL PARK, Jl. Letjen S. Parman Komp. Podomoro, RT. 9/RW 5, Tj. Duren Selatan, Grogol Petamburan, Kota Jakarta Barat, DKI Jakarta, 11470.
- **USD 账户**：
  - 银行名称：BNI (BANK NEGARA INDONESIA)
  - 账号名称：PT SHAN HAI MAP
  - 银行账号：6060677669
  - SWIFT：BNINIDJAXXX
  - 分行名称：BNI APL TOWER Grogol petamburan Jakarta Barat
- **RMB 账户**：
  - 银行名称：ICBC (KCP CENTRAL PARK)
  - 账号名称：PT.SHAN HAI MAP
  - 银行账号：0120020400000811366
  - SWIFT：ICBKIDJAXXX
  - 分行名称：ICBC (CENTRAL PARK BRANCH)
- ⚠️ 户名统一为 `PT. SHAN HAI MAP`（**带点**）：刘旭 2026-09-13 确认「雅加达公司无论什么币种，开户名称都是 PT. SHAN HAI MAP」。三个币种账户的**银行/账号/SWIFT 按币种不同**，只有户名写法一致。
  注：`verify_quotation.py` 的 `normalize_company_name()` 对点号不敏感（三种写法都能过校验），所以户名写错不会被 verify 拦下——必须靠 `config/entities.json` 里的数据正确

### beijing
- 账户名称：北京山海图科技有限公司
- 税号：91110108080546395Q
- 开户银行：华夏银行(北京学院路华夏银行支行)
- 银行账号：10242000000049937
- 地址：北京市海淀区西四环北路158号1幢一层3-65
- ⚠️ 无 SWIFT CODE

### xian
- 账户名称：北京山海图科技有限公司西安分公司
- 税号：91610131MAB11JW331
- 开户行：中国银行西安高新技术开发区支行
- 账号：1021 0955 7761
- 联行开户银行：1047 9100 5904
- 国际银行代码 SWIFT CODE：BKCHCNBJ620
- 地址：西安市高新区科技路林凯国际大厦15层1501-01-03室

### shenzhen
- 账户名称：北京山海图科技有限公司深圳分公司
- 税号：91440300MA5HXMAEXM
- 开户行：中国银行股份有限公司深圳高新区支行
- 账号：7770 7729 1133
- 联行开户银行：104584002687
- 国际银行代码 SWIFT CODE：BKCHCNBJ45A
- 地址：深圳市南山区招商街道花果山社区南海大道1052号至卓飞高大厦(海翔广场)717

### shanghai
- 账户名称：北京山海图科技有限公司上海分公司
- 税号：91310118MAEGFAM57M
- 账号：4351 8873 3108
- 开户银行：中国银行上海市虹桥会展中心支行
- 行号：104290020130
- SWIFT CODE：BKCHCNBJ300
- 地址：上海市闵行区虹桥LM世界中心L3-B栋 305A

### shanghai_new
- 账户名称：上海山海图新企业咨询有限公司
- 税号：91310113MAEW51431Q
- 账号：4520 8977 3373
- 开户行：中国银行股份有限公司上海市虹桥会展中心支行
- 行号：104290020130
- SWIFT CODE：BKCHCNBJ300
- 地址：上海市青浦区虹桥LM世界中心L3-B栋 305A

### singapore
- Beneficiary Name: SHAN HAI MAP CONSULTANCY PTE.LTD
- Beneficiary Bank: Oversea-Chinese Banking Corporation Limited (OCBC)
- Bank Address: 63 Chulia Street, #10-00, OCBC Centre, Singapore 049514
- SWIFT Code: OCBCSGSG
- Bank Code: 7339
- SGD Account No: 701714438001
- Multi-Currency Account No (RMB/USD): 687482901201
- 公司地址: 1 North Bridge Road, #06-14 High Street Centre, SINGAPORE 179094

### deyin
- 公司地址: OFFICE TOWER 3 CIPUTRA INTERNATIONAL LT.5, JL. LINGKAR LUAR BARAT BLOK A NO.1, JAKARTA BARAT 11740
- **IDR 账户**：
  - 银行名称 Nama Bank：BCA (BANK CENTRAL ASIA)
  - 银行账号 No. Rek：6802 044 409
  - 国际银行代码 Swift Code：CENAIDJAXXX
  - 账号名称 Atas Nama：PT DEIN TALENT SOLUTIONS
  - 分行名称 Nama Cabang：BCA KCU BINTARO
- **USD 账户**：
  - 银行名称 Nama Bank：BNI (BANK NEGARA INDONESIA)
  - 银行账号 No. Rek：2051075234
  - 国际银行代码 Swift Code：BNINIDJAXXX
  - 账号名称 Atas Nama：PT DEIN TALENT SOLUTIONS
  - 分行名称 Nama Cabang：BNI APL TOWER Grogol petamburan Jakarta Barat

### thailand
- Beneficiary Name: SHAN HAI MAP (THAILAND) CO., LTD.
- Beneficiary Bank: Kasikorn PCL. Thailand, Central Rama 9 Branch (847)
- Beneficiary Bank Address: 9/9 Central Plaza Tower, 5 Floor, Room 512-513, Rama9 rd Huaykhwang, HuayKwang BKK 10310
- Beneficiary Bank Swift Code: KASITHBK
- Account Number: 1931179981
- 公司地址: Thanapoom Tower, 25th floor Unit A2, 1550 New Petchaburi Rd, Khwaeng Makkasan, Khet Ratchathewi, Bangkok 10400

### vietnam
- 公司名称: CÔNG TY TNHH SHANHAIMAP VIỆT NAM
- 中文名: 山海图越南有限公司
- **VND 账户**：
  - 银行名称 Tên Ngân hàng：Ngân hàng TMCP Ngoại thương Việt Nam - Chi nhánh Thăng Long
  - 账号名称 Chủ tài khoản：Cong ty TNHH Shanhaimap Viet Nam
  - 银行账号 Tài khoản：104 799 1200 (VND)
  - 国际银行代码 Mã ngân hàng quốc tế (SWIFT)：BFTVVNVX
  - 分行地址 Địa chỉ：Tòa Nhà Pvoil Phú Thọ, Số 148 Hoàng Quốc Việt, Phường Nghĩa Tân, Quận Cầu Giấy, Thành Phố Hà Nội
- **USD / RMB 账户**（共用同一账号）：
  - 银行名称 Tên Ngân hàng：Ngân hàng TMCP Ngoại thương Việt Nam - Chi nhánh Thăng Long
  - 账号名称 Chủ tài khoản：Cong ty TNHH Shanhaimap Viet Nam
  - 银行账号 Tài khoản：104 799 1540 (USD)
  - 国际银行代码 Mã ngân hàng quốc tế (SWIFT)：BFTVVNVX
  - 分行地址 Địa chỉ：Tòa Nhà Pvoil Phú Thọ, Số 148 Hoàng Quốc Việt, Phường Nghĩa Tân, Quận Cầu Giấy, Thành Phố Hà Nội

### egypt
- 公司名称: SHAN HAI MAP FOR CONSULTING CO
- 公司地址: 埃及新开罗90号大街TOP90 1层134室
- **EGP 账户**：
  - Bank Name: Qatar National Bank (QNB) - 00037
  - Bank Branch: 00177
  - SWIFT CODE: QNBAEGCXXXX
  - Beneficiary Name: SHAN HAI MAP FOR CONSULTING CO
  - Account Number: 20317533309
  - IBAN: EG470037017708182031753330905
  - Bank Address: Top 90 Mall, unit 133&134, S Teseen, New Cairo 1, Cairo Governorate, EGYPT
- **USD / RMB 账户**（共用同一账号）：
  - Bank Name: Qatar National Bank (QNB) - 00037
  - Bank Branch: 00177
  - SWIFT CODE: QNBAEGCXXXX
  - Beneficiary Name: SHAN HAI MAP FOR CONSULTING CO
  - Account Number: 20317533321
  - IBAN: EG690037017708402031753332119
  - Bank Address: Top 90 Mall, unit 133&134, S Teseen, New Cairo 1, Cairo Governorate, EGYPT

### malaysia
- 公司名称: SHANHAIMAP SDN. BHD.
- 公司地址: Suite 16-03A & 05, Level 16, Wisma UOA II No. 21, Jalan Pinang, 50450 Kuala Lumpur, Malaysia
- Account Name: SHANHAIMAP SDN. BHD.
- Account Number: 7011647369
- Beneficiary Bank: OCBC Bank (Malaysia) Bhd
- Branch: KL MAIN
- Swift code: OCBCMYKL
- Bank Address: INFINITY TOWER, JALAN SS 6/3, KELANA JAYA, 47301 PETALING JAYA SELANGOR MALAYSIA
