# Ginger

Ginger は、Python で実装された独自のプログラミング言語処理系です。AI と人間が設計意図を共有し、関数の宣言・実装・失敗の可能性を記録することを目指しています。

現在は開発途中で、仕様も一部未確定です。この README は現時点の実装状況を記録したもので、すべての挙動を将来も維持する正式仕様ではありません。

## 実行方法

プロジェクトルートで、Python 3.10 以降を使って実行します。現行コードは Python 標準ライブラリを使用しています。

```sh
python3 -B -m ginger.main
```

現在の `ginger/main.py` は `ginger/script/Scene_9.ginger` を固定で読み込み、`3` を出力します。入力ファイルを引数で指定する CLI はありません。`-B` は Python のバイトコードキャッシュ生成を抑止します。

別のサンプルは Python API から実行できます。次の例もプロジェクトルートで実行します。

```sh
python3 -B - <<'PY'
from pathlib import Path
from ginger.pipeline import run

run(Path("ginger/script/Scene_7.ginger").read_text(encoding="utf-8"))
PY
```

`Code.ginger` と Scene 3・5・6・7・8・9 は現在実行できます。ほかのサンプルには現行構文や標準定義と一致しないものがあります。

## 処理フロー

`ginger/pipeline.py` が処理をまとめています。

```text
ソース → parse → lower → typecheck → eval
```

1. `parse`: 字句解析・構文解析を行い、AST（構文木）を生成します。
2. `lower`: 括弧内の演算子式を `add`・`sub`・`mul`・`div` の呼び出しへ変換します。
3. `typecheck`: 標準 JSON catalog とソースの宣言を基に、型・宣言の対応・制約を検査し、未処理 failure を警告します。
4. `eval`: ユーザー定義関数や Python の builtin 実装で評価します。

`compile(src)` は型検査済み AST を返し、`execute(prog)` が評価します。`run(src)` は両方を実行します。型検査の通過だけで、未完成部分を含むすべての実行時契約が保証されるわけではありません。

## 現在確認できる主な機能

| 機能 | 現在の範囲 |
|---|---|
| `let` / `var` | 型注釈付きの変数宣言。再代入は `var` のみ |
| Int / Float | 数値リテラル、加減乗算、Float 除算、`neg`、`toFloat` |
| Bool / Ordering / Unit | 比較結果、出力、値を返さない処理 |
| `sig` / `func` | 関数の型宣言と実装を分け、引数と戻り値を検査 |
| `guarantee` / `impl` | メソッドの宣言、型への保証登録、builtin への対応付け |
| `register` | メソッドを持たない guarantee への型の登録 |
| `typegroup` | 型名の集合と `require T in Group` による所属検査 |
| `failure` / `try` / `catch` | 失敗可能性の宣言・警告と、トップレベルでの捕捉 |
| `Thunk` / `thunk` / `force` | 式の遅延評価と強制評価 |
| 標準 JSON catalog | 数値演算・変換・比較・出力・遅延評価の宣言を自動注入 |

## 型・変数・式

トップレベル変数には明示的な型注釈が必要です。通常の型照合では Int / Float の暗黙変換を行いません。Float が必要なら Float リテラルか `toFloat` を使います。

```ginger
let initial: Int = 1
var x: Int = (initial + 2)
x = (x * 3)
print(x)
var y: Float = div(toFloat(x), 2.0)
print(y)
var negative: Int = neg(x)
print(negative)
```

この例の出力は `9`、`4.5`、`-9` です。

- 中置演算は現在 `(a + b)` のように括弧内で使用します。`*`・`/` は `+`・`-` より優先されます。
- 単なる `(1)` や `(f())` は認めず、括弧内に中置演算を要求します。
- 単項マイナスは使えません。`-x` の代わりに `neg(x)` を使います。
- 除算は標準では Float 同士に限定します。`div(1, 2)` は拒否されます。
- Bool 値は現在 `eq`・`lt` などから得ます。`true` / `false` のリテラルはありません。
- `cmp(a, b)` は `a > b` で `Left`、等しければ `Flat`、`a < b` で `Right` を返します。
- Unit の実行値は Python の `None` です。トップレベルの式文は Unit を要求しますが、関数本体では非 Unit の式文も通るという不一致が残っています。

内部の型情報は、型名と再帰的な型引数を持つ `TypeRef` で表します。型引数の list / tuple 表現が混在していますが、構造比較はそのコンテナの違いを無視します。

## 関数の宣言と実装

```ginger
sig convert(Int) -> Float {
    failure Never
}
func convert(value: Int) {
    return toFloat(value)
}
var result: Float = convert(2)
print(result)
```

この例は `2.0` を出力します。現在採用している sig / func の対応規則は次のとおりです。

- 同名の `sig` が `func` より先に存在する必要があります。
- 引数数が等しく、各位置の型が型名と再帰的な型引数まで一致する必要があります。
- `Thunk[Int]` と `Thunk[Float]` は異なる型として照合します。
- 引数名は照合に使用しません。sig は引数型のみを持ちます。
- 型変数名の読み替えは行いません。`T` と `U` は同一視しません。
- 呼び出しは位置引数で、値も位置順に束縛します。同名 sig のオーバーロードはありません。
- return の型は sig の戻り値と構造比較します。return がなければ Unit を要求します。

型変数は現在、原則として1文字の大文字で表します。`T → T` の恒等関数は動作しますが、一般的なジェネリクスや型推論は限定実装です。例えば `var x: Int = add(1, 2)` は通りますが、`print(add(1, 2))` は内側の戻り値型を決められず拒否されます。

## guarantee と標準 catalog

`guarantee` はメソッドを宣言し、`impl` は型・保証・メソッドから builtin への対応を登録します。現在検証するのは、保証の存在、必要メソッドの存在、builtin 名の存在などです。処理の意味や builtin の実際の型契約までは検証していません。

`require T guarantees G` は型の保証登録を、`require T in Group` は typegroup への所属を検査します。メソッドを持つ guarantee には `impl` が必要で、`register` はメソッドを持たない保証に使います。

`ginger/catalog/` の `math`・`cast`・`ordering`・`io`・`lazy` は `core/prelude.py` から自動で読み込みます。JSON の宣言は AST に変換され、ソースの宣言とともにシンボルを構築します。

実行時は `thunk` / `force` を特別扱いし、通常の呼び出しは同名の `func`、sig 直結の builtin、guarantee 経由の impl の順に処理します。最後の経路は保証1個と先頭引数の実行時型を前提にしています。

現在は単一ソースに宣言と実行文を記述できます。`Catalog.ginger`・`Code.ginger`・`Impl.ginger` を役割別に自動読み込みする仕組みはありません。Catalog とソースの責務や標準実装の置換は未確定です。

## failure と捕捉

sig 本体に `failure DivideByZero` などを列挙できます。現在は省略可能で、省略とソース中の `failure Never` は空の failure 集合として扱います。使える failure 名は `core/failure_spec.py` に定義されたものに限られます。

```ginger
try print(div(1.0, 0.0))
catch DivideByZero print(0)
```

この例は `0` を出力します。

- `try` の後には1個以上の `catch` が必要です。対象は Unit 式に限定され、関数本体内では使えません。
- 静的には sig と引数式の failure を合算し、未処理分を警告します。警告だけでは実行を止めません。
- 実行時は一致する最初の catch を処理します。未捕捉の `RaisedFailure` は外へ伝播します。
- catch 内で同種の failure が再発生した場合、現在は握りつぶします。
- sig の `@attr.handled` は Unit を要求し、静的にはその関数自身の failure を除外します。ユーザー関数では本体の `RaisedFailure` を捕捉しますが、builtin 経路や func だけに付けた属性では同じ効果になりません。

failure の契約には既知の不整合があります。標準 `div` の failure 宣言は空ですが、ゼロ除算時には `DivideByZero` が発生します。関数本体の失敗と sig の宣言も照合していません。明示義務、宣言の保証範囲、未処理時の扱い、handled の意味は設計判断が必要です。Never と他の failure の混在検査にも記述順による不具合が残っています。

## Thunk と遅延評価

```ginger
var x: Int = 1
var t: Thunk[Int] = thunk(x)
x = 2
print(force(t))
print(x)
```

現在の出力は `1`、`2` です。

`thunk(expr)` は型検査後、実行時には式を評価せず保存します。`force(t)` は呼ぶたびに保存した式を再評価し、結果をキャッシュしません。どちらも引数は1個です。force の戻り値は期待型がある文脈では型照合します。

現在は環境辞書を浅くコピーし、外側の環境は参照で保持します。トップレベルの再代入は Cell を置き換えるため、上の例では Thunk が作成時の値を読みます。これはすべての値を深くコピーするという保証ではありません。

遅延式での実際の failure は force 時に発生します。一方、静的な failure 解析は通常の引数式と同様に扱うため、作成側に警告が付き、保存された Thunk の failure 情報を force 側で復元できません。捕捉と failure の仕様は未確定です。

## 現在の制限・未確定事項

| 項目 | 制限・判断が必要な点 |
|---|---|
| 型変数推論 | 戻り値が型変数の通常呼び出しは期待型を要求。return 式に sig の期待型を伝播しない |
| 型引数・ジェネリクス | 再帰的な型変数置換は未対応。通常呼び出しで戻り値の型引数を失う経路がある |
| guarantee の契約 | 型変数の保証を本体内の呼び出し検査に十分反映できない。複数保証やメソッドの型契約と実装選択の関係も未確定 |
| failure の契約 | 宣言と実行の整合、handled、警告、捕捉後の失敗の扱いが未確定 |
| Thunk | 捕捉の意味と遅延中の failure の保持・伝播が未確定 |
| Catalog とソース | 役割の強制や複数ファイルの読み込みはない |
| 名前付き引数 | 構文解析は対応するが、通常の型検査・実行では拒否 |
| スコープ | 関数外変数への参照は通常の型検査で拒否。評価器には外側環境を参照する処理が残る |
| 関数内ローカル変数 | let / var、再代入は未対応。本体は return と式文のみ |
| CLI | main がサンプル固定。入力パス指定の CLI は未実装 |
| String・制御構文など | 文字列リテラル、条件分岐、ループ、ユーザー定義データ構造は未実装。String の実行時処理や builtin は一部存在する |

未実装項目は、今後追加すべき必須機能を意味しません。対象に含めるかどうかも含め、別途判断が必要です。

## プロジェクト構成

| 場所 | 役割 |
|---|---|
| `ginger/main.py` / `pipeline.py` | サンプルの起動と処理フロー |
| `ginger/tokenizer.py` / `parser.py` | 字句解析・構文解析 |
| `ginger/ast.py` / `lower.py` | AST・型参照の定義と演算子式の変換 |
| `ginger/symbols_builder.py` | 宣言の収集、sig / func の対応・catalog の検証 |
| `ginger/typecheck.py` | 型・制約・failure の静的検査 |
| `ginger/eval.py` / `builtin.py` | 評価と Python の組み込み実装 |
| `ginger/runtime/` | Thunk・実行時 failure・ディスパッチ補助 |
| `ginger/catalog/` / `core/` | 標準 JSON 定義、読み込み、failure 定義 |
| `ginger/script/` | 現行の動作例と過去の試行サンプル |

## 開発上の注意

この README は現時点の実装状況の記録です。未確定事項はコードや README のどちらかを正と決めつけず、設計判断を先に行います。

古い `catalog` / `fn` / `args:` 構文、`run_ginger.py` による起動、Catalog / Code の自動分離は現行の使い方ではありません。`script/` の古いサンプルやコメントにも不一致があります。既存ファイルの存在だけを、対応機能や互換性の保証とは扱わないでください。
