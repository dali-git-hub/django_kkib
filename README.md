# django_kkib（家計簿アプリ）

マスコミ業界でDX関連業務などに携わりながら、業務外で開発している家計簿アプリです。  
日々のレシートを撮影・登録して家計を見える化し、成長次第で消費動向分析まで可能なプラットフォーム化、さらには公共政策のアジェンダ提供にもつなげていくことを目標にしています。

This is a personal household accounting web application built with Django.
I am developing it outside of work to learn full-stack web development and
data-driven analysis for public policy.

## 技術スタック

- 言語: Python
- フレームワーク: Django
- データベース: SQLite（開発中）
- テンプレート: Django Templates（`templates/` 配下）
- その他: Git / GitHub / ChatGPT を活用して開発

今後は Docker・AWS などを使ったデプロイも学習予定です。

## 主な機能（開発中を含む）

- レシート画像からの支出登録（今後OCRの精度向上）
- 支出の一覧表示（今月の支出・収入・収支）
- 費目（カテゴリ）ごとの管理
- 支出データの編集・削除
- 月単位での集計表示（家計簿としての基本機能）

## こだわった点

- 自分や家族が実際に使えることを前提に、入力項目や画面遷移を設計
- 「家計簿アプリ」で終わらず、将来的に
  - 家計の傾向の可視化
  - ニュース・政策情報との連携→公共政策への提言
  に発展させられるよう、データ構造やテーブル設計を試行錯誤しています。
- 事務職としての業務フロー改善の経験を活かし、
  日常の「レシート入力」という作業負担をできる限り減らすことを意識しています。

## 技術的に苦労した点

- Djangoでの家計データのモデリング（支出項目、カテゴリ、日付、画像との紐付け）
- レシート合計と明細の整合性をとるバリデーションロジック
- 月別の集計・サマリを分かりやすく表示するためのビューとテンプレート構成

コード提案は生成AI（ChatGPT / GitHub Copilot）によるものを参考にしつつ、  
動作確認や画面設計、アプリ全体の構想は自分で行っています。

## 今後の予定

- Docker 化と本番環境（AWS 等）へのデプロイ
- OCR処理（Google Vision API / AWS Rekognition）組み込みによる精度の向上
- グラフ表示など、家計の傾向を直感的に把握できるダッシュボードの追加
- テストコードやLintツールの導入

## 動作環境とセットアップ

- Python 3.x
- Django 〇.x

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
