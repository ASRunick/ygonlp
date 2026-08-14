# 価格分析の拡張特徴量

`analyze-prices` は、テキスト長だけのベースラインと、再現可能な非テキスト特徴量を追加した線形回帰を vendor/currency ごとに比較します。結果は同一snapshot内の探索的・in-sampleな記述であり、原因や将来価格を示しません。

最初の特徴量は `card_age_days` です。検証済みmeasurement JSONLの候補TCG初出日とprice snapshotのtimestampから算出します。欠損またはsnapshot後の日付はcomplete caseから除外し、件数を記録します。

banlist、トーナメント利用、最近のarchetype support、reprint history、rarity、vendor availabilityは、現行の検証済み入力だけでは再現不能です。外部ソースを導入する場合は、取得条件・時点・ライセンス・再配布可否を別途確認し、無許可スクレイピングを行いません。
