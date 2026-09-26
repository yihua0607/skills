# 实体银行信息对照

> 此文件由 `scripts/generate_bank_reference.py` 根据 `config/entities.json` 自动生成，请勿手工编辑。

## jakarta

- 签约主体：PT. SHAN HAI MAP
- 默认币种：IDR
- 允许币种：IDR / RMB / USD
- SWIFT 政策：required

### 银行说明

- IDR、USD、RMB 使用不同银行账户；账户名称统一为 PT. SHAN HAI MAP。

### IDR 账户

- 银行名称：BCA (KCP CENTRAL PARK)
- 账号名称：PT. SHAN HAI MAP
- 银行账号：5485225789
- 国际银行代码 Swift Code：CENAIDJA
- 分行名称：KCP CENTRAL PARK, Jl. Letjen S. Parman Komp. Podomoro, RT. 9/RW 5, Tj. Duren Selatan, Grogol Petamburan, Kota Jakarta Barat, DKI Jakarta, 11470.

### USD 账户

- 银行名称：BNI (BANK NEGARA INDONESIA)
- 账号名称：PT. SHAN HAI MAP
- 银行账号：6060677669
- 国际银行代码 Swift Code：BNINIDJAXXX
- 分行名称：BNI APL TOWER Grogol petamburan Jakarta Barat

### RMB 账户

- 银行名称：ICBC (KCP CENTRAL PARK)
- 账号名称：PT. SHAN HAI MAP
- 银行账号：0120020400000811366
- 国际银行代码 Swift Code：ICBKIDJAXXX
- 分行名称：ICBC (CENTRAL PARK BRANCH)

## sci

- 签约主体：PT SHM CONSULTING INDONESIA
- 默认币种：IDR
- 允许币种：IDR / RMB / USD
- SWIFT 政策：required

### 银行说明

- IDR、USD、RMB 共用同一个多币种账户。

### 通用账户

- 银行名称：Bank OCBC Indonesia (PT Bank OCBC NISP Tbk)
- 账号名称：PT SHM CONSULTING INDONESIA
- 银行账号：545800133641
- 国际银行代码 Swift Code：NISPIDJA
- 分行地址：Jl. Prof.Dr.Satrio No 25, Jakarta Selatan 12940, Indonesia

## beijing

- 签约主体：北京山海图科技有限公司
- 默认币种：RMB
- 允许币种：RMB / USD
- SWIFT 政策：not_required

### 银行说明

- 北京主体不使用 SWIFT CODE。

### 通用账户

- 账户名称：北京山海图科技有限公司
- 税号：91110108080546395Q
- 开户银行：华夏银行(北京学院路华夏银行支行)
- 银行账号：10242000000049937
- 地址：北京市海淀区西四环北路158号1幢3层3-65

## xian

- 签约主体：北京山海图科技有限公司西安分公司
- 默认币种：RMB
- 允许币种：RMB / USD
- SWIFT 政策：required

### 通用账户

- 账户名称：北京山海图科技有限公司西安分公司
- 税号：91610131MAB11JW331
- 开户行：中国银行西安高新技术开发区支行
- 账号：1021 0955 7761
- 联行开户银行：1047 9100 5904
- 国际银行代码 SWIFT CODE：BKCHCNBJ620
- 地址：陕西省西安市高新区科技路林凯国际大厦15层01-03室

## shenzhen

- 签约主体：北京山海图科技有限公司深圳分公司
- 默认币种：RMB
- 允许币种：RMB / USD
- SWIFT 政策：required

### 通用账户

- 账户名称：北京山海图科技有限公司深圳分公司
- 税号：91440300MA5HXMAEXM
- 开户行：中国银行股份有限公司深圳高新区支行
- 账号：7770 7729 1133
- 联行开户银行：104584002687
- 国际银行代码 SWIFT CODE：BKCHCNBJ45A
- 地址：深圳市南山区招商街道南海大道1048号海翔广场717室

## shanghai

- 签约主体：北京山海图科技有限公司上海分公司
- 默认币种：RMB
- 允许币种：RMB / USD
- SWIFT 政策：required

### 通用账户

- 账户名称：北京山海图科技有限公司上海分公司
- Account Name: Beijing Shanhaitu Technology Co., Ltd. Shanghai Branch
- 税号：91310118MAEGFAM57M
- 账户号码：4351 8873 3108
- 开户银行：中国银行上海市虹桥会展中心支行（Bank of China Shanghai Branch Hongqiao Exhibition And Convention Center Sub-Branch）
- 行号：104290020130
- SWIFT CODE：BKCHCNBJ300
- 地址：上海市青浦区虹桥LM世界中心L3-B栋 305A

## shanghai_new

- 签约主体：上海山海图新企业咨询有限公司
- 默认币种：RMB
- 允许币种：RMB / USD
- SWIFT 政策：required

### 通用账户

- 账户名称：上海山海图新企业咨询有限公司
- 税号：91310113MAEW51431Q
- 账户号码：4520 8977 3373
- 开户银行：中国银行股份有限公司上海市虹桥会展中心支行
- 行号：104290020130
- SWIFT CODE：BKCHCNBJ300

## singapore

- 签约主体：SHAN HAI MAP CONSULTANCY PTE.LTD
- 默认币种：SGD
- 允许币种：SGD / RMB / USD
- SWIFT 政策：required

### 银行说明

- SGD 使用 SGD Current Account；RMB、USD 使用 Multi-currency Account。

### 通用账户

- Beneficiary Name: SHAN HAI MAP CONSULTANCY PTE.LTD
- Beneficiary Bank: Oversea-Chinese Banking Corporation Limited
- Beneficiary Bank Address: 63 Chulia Street, #10-00, OCBC Centre Singapore 049514
- Beneficiary Bank Swift Code: OCBCSGSG
- OCBC Bank Code: 7339
- OCBC Branch Code: First 3 digits of your account number
- Multi-currency Account: 687482901201
- SGD Current Account: 701714438001

## deyin

- 签约主体：PT DEIN TALENT SOLUTIONS
- 默认币种：IDR
- 允许币种：IDR / USD
- SWIFT 政策：required

### 银行说明

- IDR 与 USD 使用不同银行账户。

### IDR 账户

- 银行名称 Nama Bank：BCA (BANK CENTRAL ASIA)
- 银行账号 No. Rek：6802 044 409
- 国际银行代码 Swift Code：CENAIDJAXXX
- 账号名称 Atas Nama：PT DEIN TALENT SOLUTIONS
- 分行名称 Nama Cabang：BCA KCU BINTARO

### USD 账户

- 银行名称 Nama Bank：BNI (BANK NEGARA INDONESIA)
- 银行账号 No. Rek：2051075234
- 国际银行代码 Swift Code：BNINIDJAXXX
- 账号名称 Atas Nama：PT DEIN TALENT SOLUTIONS
- 分行名称 Nama Cabang：BNI APL TOWER Grogol petamburan Jakarta Barat

## slf

- 签约主体：Hukum SEA Firma
- 默认币种：IDR
- 允许币种：IDR / USD
- SWIFT 政策：required

### 银行说明

- IDR 与 USD 使用同一银行账户。

### IDR 账户

- 银行名称 Nama Bank：BNI Branch Central Park Mall
- 银行账号 No. Rek：1882660986
- 国际银行代码 Swift Code：BNINIDJAXXX
- 账号名称 Atas Nama：Hukum SEA Firma

### USD 账户

- 银行名称 Nama Bank：BNI Branch Central Park Mall
- 银行账号 No. Rek：1882660986
- 国际银行代码 Swift Code：BNINIDJAXXX
- 账号名称 Atas Nama：Hukum SEA Firma

## stc

- 签约主体：PT. SHM TAX CONSULTING
- 默认币种：IDR
- 允许币种：IDR / USD
- SWIFT 政策：required

### 银行说明

- IDR 与 USD 使用同一银行账户。
- 报价汇总按税前金额加 11% 增值税，再扣减 2% PPH23。

### IDR 账户

- 银行名称：BCA (KCP CENTRAL PARK)
- 账号名称：PT. SHM TAX CONSULTING
- 银行账号：5485483133
- 国际银行代码 Swift Code：CENAIDJA
- 分行名称：KCP CENTRAL PARK, Jl. Letjen S. Parman Komp. Podomoro, RT. 9/RW 5, Tj. Duren Selatan, Grogol Petamburan, Kota Jakarta Barat, DKI Jakarta, 11470.

### USD 账户

- 银行名称：BCA (KCP CENTRAL PARK)
- 账号名称：PT. SHM TAX CONSULTING
- 银行账号：5485483133
- 国际银行代码 Swift Code：CENAIDJA
- 分行名称：KCP CENTRAL PARK, Jl. Letjen S. Parman Komp. Podomoro, RT. 9/RW 5, Tj. Duren Selatan, Grogol Petamburan, Kota Jakarta Barat, DKI Jakarta, 11470.

## thailand

- 签约主体：SHAN HAI MAP (THAILAND) CO., LTD.
- 默认币种：THB
- 允许币种：THB / RMB / USD
- SWIFT 政策：required

### 通用账户

- Beneficiary Name: SHAN HAI MAP (THAILAND) CO., LTD.
- Beneficiary Bank: Kasikorn PCL. Thailand, Central Rama 9 Branch (847)
- Beneficiary Bank Address: 9/9 Central Plaza Tower, 5 Floor, Room 512-513, Rama9 rd Huaykhwang, HuayKwang BKK 10310
- Beneficiary Bank Swift Code: KASITHBK
- Account Number: 1931179981

## vietnam

- 签约主体：CÔNG TY TNHH SHANHAIMAP VIỆT NAM
- 默认币种：VND
- 允许币种：VND / RMB / USD
- SWIFT 政策：required

### 银行说明

- VND 使用本币账户；USD、RMB 共用外币账户。

### VND 账户

- 银行名称：Joint Stock Commercial Bank for Foreign Trade of Vietnam - Thang Long Branch
- 账号名称：Cong ty TNHH Shanhaimap Viet Nam
- 银行账号：104 799 1200 (VND)
- 国际银行代码 Swift Code：BFTVVNVX
- 分行地址：Pvoil Phu Tho Building, No. 148 Hoang Quoc Viet, Nghia Tan Ward, Cau Giay District, Hanoi City

### USD 账户

- 银行名称：Joint Stock Commercial Bank for Foreign Trade of Vietnam - Thang Long Branch
- 账号名称：Cong ty TNHH Shanhaimap Viet Nam
- 银行账号：104 799 1540 (USD)
- 国际银行代码 Swift Code：BFTVVNVX
- 分行地址：Pvoil Phu Tho Building, No. 148 Hoang Quoc Viet, Nghia Tan Ward, Cau Giay District, Hanoi City

### RMB 账户

- 银行名称：Joint Stock Commercial Bank for Foreign Trade of Vietnam - Thang Long Branch
- 账号名称：Cong ty TNHH Shanhaimap Viet Nam
- 银行账号：104 799 1540
- 国际银行代码 Swift Code：BFTVVNVX
- 分行地址：Pvoil Phu Tho Building, No. 148 Hoang Quoc Viet, Nghia Tan Ward, Cau Giay District, Hanoi City

## egypt

- 签约主体：SHAN HAI MAP FOR CONSULTING CO
- 默认币种：EGP
- 允许币种：EGP / RMB / USD
- SWIFT 政策：required

### 银行说明

- EGP 使用本币账户；USD、RMB 共用外币账户。

### EGP 账户

- Bank Name: Qatar National Bank (QNB) - 00037
- Bank Branch: 00177
- SWIFT CODE: QNBAEGCXXXX
- Beneficiary Name: SHAN HAI MAP FOR CONSULTING CO
- Account Number: 20317533309
- IBAN: EG470037017708182031753330905
- Bank Address: Top 90 Mall, unit 133&134, S Teseen, New Cairo 1, Cairo Governorate, EGYPT

### USD 账户

- Bank Name: Qatar National Bank (QNB) - 00037
- Bank Branch: 00177
- SWIFT CODE: QNBAEGCXXXX
- Beneficiary Name: SHAN HAI MAP FOR CONSULTING CO
- Account Number: 20317533321
- IBAN: EG690037017708402031753332119
- Bank Address: Top 90 Mall, unit 133&134, S Teseen, New Cairo 1, Cairo Governorate, EGYPT

### RMB 账户

- Bank Name: Qatar National Bank (QNB) - 00037
- Bank Branch: 00177
- SWIFT CODE: QNBAEGCXXXX
- Beneficiary Name: SHAN HAI MAP FOR CONSULTING CO
- Account Number: 20317533321
- IBAN: EG690037017708402031753332119
- Bank Address: Top 90 Mall, unit 133&134, S Teseen, New Cairo 1, Cairo Governorate, EGYPT

## malaysia

- 签约主体：SHANHAIMAP SDN. BHD.
- 默认币种：MYR
- 允许币种：MYR / RMB / USD
- SWIFT 政策：required

### 通用账户

- Account Name: SHANHAIMAP SDN. BHD.
- Account Number: 7011647369
- Beneficiary Bank: OCBC Bank (Malaysia) Bhd
- Branch: KL MAIN
- Swift code: OCBCMYKL
- Bank Address: INFINITY TOWER, JALAN SS 6/3, KELANA JAYA, 47301 PETALING JAYA SELANGOR MALAYSIA
