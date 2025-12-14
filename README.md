# Ginger

Ginger は、**言語設計そのものを試すための実験的な DSL（Domain Specific Language）**です。

構文の網羅や高速実行を目的とせず、  
**「設計（catalog）と実装（code）を分離した際の効能、および副作用」**  
を研究するためのミニマム言語です。

---

## コンセプト

- **仕様は catalog に集約する**
- **code には「使う」ことしか書かない**
- 構文・実装は最低限
- パーサや評価器は「設計検証用の治具」

Ginger において、パーサは主役ではありません。  
主役は **言語設計の仮説そのもの**です。

---

## Catalog.ginger

`Catalog.ginger` は、Ginger の世界で **何が存在できるか** を定義します。

例：
catalog Sample

type Int

fn add
args: Int, Int
return: Int
description: “Adds two integers.”

### 特徴

- 関数定義は **catalog のみ**
- `args` / `return` は必須
- `description` は任意（ドキュメント用途）
- インデントは意味を持たない（行ベース）

---

## Code.ginger

`code.ginger` には、**実行したい式だけ**を書きます。

例：
add(1, 2)

## python run_ginger.py

- `Catalog.ginger` を読み込む
- `Code.ginger` をパースする
- Catalog に基づいて関数の引数および戻り値の型をチェックする
- 最小実装された関数を評価する

---

## 型チェックについて（v0）

現在の Ginger は：

- **catalog に書かれた型名を基準にチェック**
- 型名は Python の型にマッピングして検証

例：

```python
TYPEMAP = {
    "Int": int,
}
