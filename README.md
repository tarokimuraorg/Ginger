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

`Code.ginger` と Scene 3・5・6・7・8・9 は現在実行できます。Scene 5・7 は `DivideByZero` の未処理警告がそれぞれ2件出ますが、評価結果は従来と同じです。ほかのサンプルには現行構文や標準定義と一致しないものがあります。

## 処理フロー

`ginger/pipeline.py` が処理をまとめています。

```text
ソース → parse → lower → typecheck → eval
```

1. `parse`: 字句解析・構文解析を行い、AST（構文木）を生成します。
2. `lower`: 括弧内の演算子式を `add`・`sub`・`mul`・`div` の呼び出しへ変換します。
3. `typecheck`: 標準 JSON catalog とソースの宣言を基に、型・宣言の対応・制約、通常ユーザー関数の failure 上限契約、catch の資格を検査し、トップレベルの未処理 failure を警告します。failure 上限検証には後述の保留境界があります。
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
| `failure` / `try` / `catch` | failure 上限契約の検査、静的集合に基づく捕捉制限、未処理警告。try/catch はトップレベルのみ |
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

この例の評価結果は `9`、`4.5`、`-9` です。実行前に、未捕捉の `div` 呼び出しに対する `DivideByZero` の警告も表示されます。

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

現在採用している仕様では、sig に宣言する failure は、呼び出し側が考慮すべき意味のある失敗可能性を記録する契約であり、その関数から外部へ漏れ得る failure の上限集合です。通常の検証対象では、静的に推論した本体の `inferred_failures` が宣言の `declared_failures` の部分集合でなければなりません。

```text
inferred_failures ⊆ declared_failures
```

宣言に、現行実装では実際に発生しない failure が含まれていても許可します。内部で確実に処理され、外へ漏れない failure は公開契約に含めません。実装バグや型検査で防ぐべき問題まで failure として列挙する必要はありません。

sig 本体に `failure DivideByZero` などを列挙できます。使える failure 名は `core/failure_spec.py` に定義されたものに限られます。

- failure の省略と `failure Never` は、空集合、つまり「外部へ公開する failure なし」を表します。
- Never と通常 failure の混在は、記述順によらず拒否します。
- 同一 failure の重複（Never の重複を含む）は拒否します。
- failure 名に型引数は付けられません。
- JSON catalog とソースで、Never と重複の扱いを揃えています。

通常ユーザー関数では、到達可能な return 式と式文の failure を合算し、未宣言 failure の伝播を型検査で拒否します。通常の呼び出しは callee の sig と引数式の failure を合算します。最初の無条件 return より後は failure 集計から除外しますが、既存の型検査は続けます。関数内 try/catch は未対応です。handled・Thunk に依存する関数は、後述のとおり上限検証を保留します。

標準 `div` は `DivideByZero` を宣言しています。catch せずにトップレベルで使うと未処理警告の対象になります。値に応じた failure の絞り込みは行わないため、除数がゼロでない呼び出しも警告対象です。

```ginger
try print(div(1.0, 0.0))
catch DivideByZero print(0)
```

この例は警告なしで `0` を出力します。

現在採用している catch の規則は、try 対象式から静的に発生し得ると宣言・推論された failure だけを捕捉対象として認めることです。すべての catch を、捕捉分を削除する前の元の try 集合に対して検査します。集合外の既知 failure、未知の failure 名、Never は catch できません。実行時にたまたま未宣言の `RaisedFailure` が発生しても、静的集合外の catch を許可する根拠にはしません。

- `try` の後には1個以上の `catch` が必要です。対象は Unit 式に限定され、関数本体内では使えません。
- トップレベルでは、捕捉・処理後に静的集合に残る failure を警告します。警告だけでは実行を止めません。これは関数本体の上限契約違反を型エラーにする規則とは別です。
- 実行時は一致する最初の catch を処理します。未捕捉の `RaisedFailure` は外へ伝播します。
- 同名 catch の重複は現在も許可します。
- handler 内で同種の failure が再発生すると現在は握りつぶします。別種は外へ伝播し、後続の catch では処理しません。

重複 catch と handler の再失敗規則は既存挙動の記録であり、今回の上限契約整備で将来の仕様まで確定したものではありません。

builtin の宣言は静的解析が信頼する契約であり、Python 実装から実際の failure を自動推論・検証してはいません。独自 builtin sig の宣言漏れも検出できません。print / IO / toFloat の failure 契約は未確定のままです。現在、標準 print の出力失敗や巨大な Int の toFloat 変換失敗は Python 例外として伝播し、Ginger の catch が捕捉する `RaisedFailure` には変換されません。

## @attr.handled と検証保留

`@attr.handled` は既存機能としてコード上に残っていますが、中核 failure 仕様としては保留中です。意味、sig / func のどちらに付けるべきか、builtin との整合性、未宣言 failure の処理を許すかは、今回の仕様では確定していません。failure 上限契約の一般則には組み込んでいません。

既存挙動として、sig の handled は Unit を要求し、呼び出し式の静的集合から callee 自身の failure を除外します。引数評価の failure は残ります。ユーザー関数では本体の `RaisedFailure` を捕捉しますが、builtin 経路や func のみに付けた属性では同じ効果になりません。この不整合は未修正です。handled を理由とする catch 資格の例外もありません。

sig / func の handled、または handled 付き callee に依存する関数は、新しい本体上限検証を保留します。保留は到達可能な呼び出しを通じて間接的な依存先からも伝わります。型検査は継続しますが、本体全体の上限契約を完全に検証済みとは扱いません。Thunk による保留も同様です。

保留理由は Diagnostics に `FAILURE_CONTRACT_DEFERRED` の note として記録します。既存 pipeline が表示するのは warning のみなので、この note は通常の実行出力には表示されません。保留を「failure が空」や「契約検証成功」と同一視しないでください。

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

Thunk の潜在 failure 契約は未確定です。遅延式での実際の failure は force 時に発生しますが、現在の静的解析は thunk の引数式を通常の引数と同様に集計するため、作成側に警告が付く場合があります。保存済み Thunk の failure 情報を force 側で静的に復元できない既知制限があります。

そのため、`print(force(t))` のように、保存済み Thunk を Unit 式の内部で force する場合でも、捕捉したい failure が静的集合に現れず、catch が拒否される場合があります。`force(thunk(expr))` のように直接書いた場合に引数の解析で failure が残ることはありますが、正式な Thunk failure 仕様として確定していません。

`thunk` / `force` を使う関数、sig の引数・戻り値に Thunk を含む関数、およびそれらに到達可能な呼び出しを通じて依存する関数は、本体の failure 上限検証を保留します。潜在 failure を保持する型表現や実行時情報はまだ導入していません。

## 現在の制限・未確定事項

| 項目 | 制限・判断が必要な点 |
|---|---|
| 型変数推論 | 戻り値が型変数の通常呼び出しは期待型を要求。return 式に sig の期待型を伝播しない |
| 型引数・ジェネリクス | 再帰的な型変数置換は未対応。通常呼び出しで戻り値の型引数を失う経路がある |
| guarantee の契約 | 型変数の保証を本体内の呼び出し検査に十分反映できない。複数保証やメソッドの型契約と実装選択の関係も未確定 |
| failure の契約 | 通常関数の上限検証と catch の静的集合制限は導入済み。builtin 実装の自動検証はなく、print / IO / toFloat の契約は未確定。未処理は警告、重複 catch・handler 再失敗は既存挙動を維持 |
| handled | 中核仕様として保留。意味・付与先・builtin との整合性は未確定で、依存する関数の上限検証は保留 |
| Thunk | 潜在 failure の保持・伝播は未確定。保存済み値の force で情報を復元できず、catch が拒否され得る |
| Catalog とソース | 役割の強制や複数ファイルの読み込みはない |
| 名前付き引数 | 構文解析は対応するが、通常の型検査・実行では拒否 |
| スコープ | 関数外変数への参照は通常の型検査で拒否。評価器には外側環境を参照する処理が残る |
| 関数内ローカル変数 | let / var、再代入は未対応。本体は return と式文のみ |
| CLI | main がサンプル固定。入力パス指定の CLI は未実装 |
| String・制御構文など | 文字列リテラル、条件分岐、ループ、ユーザー定義データ構造は未実装。String の実行時処理や builtin は一部存在する |

未実装項目は、今後追加すべき必須機能を意味しません。対象に含めるかどうかも含め、別途判断が必要です。

## テスト

failure 契約の自動回帰テストは `tests/test_failure_contract.py` にあります。上限契約、catch 資格、宣言正規化、handled / Thunk / builtin の検証境界と、既存の型検査・サンプルの回帰確認を担います。

```sh
python3 -B -m unittest discover -s tests -v
```

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
| `tests/test_failure_contract.py` | failure 契約・検証境界・既存動作の自動回帰テスト |

## 開発上の注意

この README は現時点の実装状況の記録です。未確定事項はコードや README のどちらかを正と決めつけず、設計判断を先に行います。

古い `catalog` / `fn` / `args:` 構文、`run_ginger.py` による起動、Catalog / Code の自動分離は現行の使い方ではありません。`script/` の古いサンプルやコメントにも不一致があります。既存ファイルの存在だけを、対応機能や互換性の保証とは扱わないでください。
