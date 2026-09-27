# Ginger

Ginger は、Python で実装された独自のプログラミング言語処理系です。AI と人間が設計意図を共有し、関数の宣言・実装・失敗の可能性を記録することを目指しています。

現在は開発途中で、仕様も一部未確定です。この README は現時点の実装状況を記録したもので、すべての挙動を将来も維持する正式仕様ではありません。

## 実行方法

プロジェクトルートで、Python 3.10 以降を使って実行します。現行コードは Python 標準ライブラリを使用しています。

```sh
python3 -B -m ginger.main
```

現在の `ginger/main.py` は `ginger/scripts/Scene_12.ginger` を固定で読み込み、標準出力へ `2`、`3` を出力します。入力ファイルを引数で指定する CLI はありません。`-B` は Python のバイトコードキャッシュ生成を抑止します。

別のサンプルは Python API から実行できます。次の例もプロジェクトルートで実行します。

```sh
python3 -B - <<'PY'
from pathlib import Path
from ginger.pipeline import run

run(Path("ginger/scripts/Scene_7.ginger").read_text(encoding="utf-8"))
PY
```

Scene 1〜7 は実行可能です。Scene 1 は IntegerOverflow の未処理警告が2件と DivideByZero が1件、Scene 7 は IntegerOverflow が1件出ます。Scene 2〜6 には未処理警告はありません。演算結果・標準出力は前段階と同じです。

## 処理フロー

`ginger/pipeline.py` が処理をまとめています。

```text
ソース → parse → lower → typecheck → eval
```

1. `parse`: 字句解析・構文解析を行い、AST（構文木）を生成します。
2. `lower`: 括弧内の演算子式を `add`・`sub`・`mul`・`div` の呼び出しへ変換します。
3. `typecheck`: 標準 JSON catalog とソースの宣言を基に、型・宣言の対応・制約、通常ユーザー関数の failure 上限契約、catch の資格を検査し、トップレベルの未処理 failure を警告します。builtin の実装契約には後述の検証境界があります。
4. `eval`: ユーザー定義関数や Python の builtin 実装で評価します。

`compile(src)` は型検査済み AST を返し、`execute(prog)` が評価します。`run(src)` は両方を実行します。型検査の通過だけで、未完成部分を含むすべての実行時契約が保証されるわけではありません。

型検査は現在、通常の `compile → execute` 経路で二重に実行します。compile は Program のみを返し、Symbols / expression_types / resolved_calls を保持しません。さらに型の正規化で AST が再構築されるため、評価開始時に runtime が使用する AST と Symbols に対して再検査し、式の ID に対応する型情報を作ります。再検査の診断は再表示しません。今回は API を維持します。将来、正規化済み Program と検査済み Symbols をまとめた compile 結果を execute へ渡せば再利用できます。

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

通常の整数リテラル `123` は Int、suffix を直接付けた `123i64` は Int64 です。`1.0i64` は無効です。範囲外リテラルは静的に拒否します。既存の leading zero の許容は維持します。

| 型 | 範囲 |
|---|---|
| Int | `-(2^53 - 1)` ～ `+(2^53 - 1)`（±9,007,199,254,740,991） |
| Int64 | `-(2^63 - 1)` ～ `+(2^63 - 1)`（±9,223,372,036,854,775,807） |

Int は、すべての有効な値を binary64 Float へ整数精度を失わず変換できる安全整数型です。Int64 は通常の signed 64-bit と異なり `-2^63` を含まない対称範囲で、最小値の negation も最大値に収まります。負数は `(-1)` / `(-1i64)` と書き、裸の `-1` は引き続き無効です。

| 暗黙変換 | 許可 |
|---|---|
| Int → Float | OK（有効な Int は精度損失・値域 overflow なし） |
| Int → Int64 | OK |
| Int64 → Float / Int | NG（小さい値でも禁止） |
| Float → Int / Int64 | NG |

トップレベル変数には明示的な型注釈が必要です。widening は変数初期化・再代入・具体型の関数引数・return の使用境界で行います。`var x: Int = 1` から `var y: Int64 = x`、`var z: Float = x` に変換しても、元の x の宣言型は Int のままです。runtime でも宣言型を保持し、Python int から Int / Int64 を推測しません。

3型は別の型です。sig / func の構造照合は厳密なままで、`Thunk[Int, Never] → Thunk[Int64, Never]` などの generic variance は導入しません。同じ型変数に対応する実引数は、以下の call-site 推論で安全な widening により統合できます。`toFloat` は引き続き `Int → Float` 専用です。

Int64 に提供する capability は `Negatable`・`Printable`・`Ord`（neg、print、cmp / eq / lt）のみです。add / sub / mul / div は未対応です。

標準 Int の **add / sub / mul は結果を範囲検査**し、Int 範囲外なら `RaisedFailure(FailureId.IntegerOverflow)` を送出します。範囲外の結果を変数や関数の戻り値へ渡しません。Int64 への自動昇格・wraparound・clamp・saturation は行いません。戻り値型は常に Int です。neg は対称範囲内に収まるため IntegerOverflow を持ちません。

範囲外リテラル（例: `9007199254740992`、`9223372036854775808i64`）は従来どおり静的エラーです。有効な入力から演算した結果の超過だけが runtime の IntegerOverflow になります。Int → Float / Int64 widening 自体に failure は追加しません。Float の add / sub / mul と Infinity の挙動も変更しません。

**generic 演算の failure 契約:** math catalog の `add(T,T) -> T` / sub / mul に一律の IntegerOverflow 宣言は追加していません。`ginger/builtin.py` の `INT_ARITHMETIC_FAILURES` に管理対象の3実装 `core.int.add/sub/mul` の契約を定義し、`effect_call` が呼び出しメタデータに保存された選択実装から契約を合算します。runtime dispatch も同じ実装を使用します。この限定的な特殊化により Int 呼び出しだけが IntegerOverflow を持ち、Float 呼び出しには混入しません。一般的な型依存 effect・overload・failure 型変数は導入していません。式全体の effect には引数評価の failure も含むため、Float 演算でもその引数が IntegerOverflow を起こす式なら catch できます。

型に基づく契約なので `add(1,2)` も IntegerOverflow の可能性を持ちます。値に基づく除外はしません。既存の catch 資格検査、関数の `inferred_failures ⊆ declared_failures` 検査、failureset、Thunk の latent failure に統合しています。余分な failure をユーザー関数が宣言する既存ルールは維持し、その宣言は呼び出し側でも有効です。

```ginger
var t: Thunk[Int, IntegerOverflow] = thunk(mul(9007199254740991, 2))
try force(t)
catch IntegerOverflow print(1)
```

return 式には宣言戻り値型を generic 型変数の推論用期待型として渡しません。`return add(x,1)` や `return neg(x)` は、実引数から型変数を決定できるため有効です。`make() -> T` のように引数側に T の根拠がなければ、外側の return 型が具体型でも拒否します。

整数生成経路の監査結果:

| 経路 | 保証・制約 |
|---|---|
| Int / Int64 literal | 静的範囲検査 |
| 標準 Int add / sub / mul | 範囲内の Int を返すか IntegerOverflow を送出 |
| Int / Int64 neg | 有効入力では対称範囲内。failure なし |
| その他の標準 builtin | div / toFloat は Float、cmp は Ordering、eq / lt は Bool、print は Unit。整数を新規生成する他の標準 builtin はない |
| 変数・引数・return・Thunk | 標準演算が範囲外の値を返さないため、その値を格納・伝播しない。入力自体は既存の型契約を信頼 |
| 独自 builtin sig / impl・Python API の直接呼び出し | 引き続き信頼境界。宣言と実装の型・値域・failure を全面検証しない。範囲外 Python int を Int として返す実装は技術的に可能 |

独自 builtin sig は自身の failure 宣言を維持し、Python 実装から自動推論しません。例えば標準 `core.int.add` を直接参照する独自 sig でも、呼び出し側へ IntegerOverflow を公開する責任は宣言側にあります。runtime の標準算術結果検査は実行されます。任意の独自 builtin が不正な Int を生成した場合は、neg・widening・toFloat などの保証の前提を破ります。builtin sandbox や全面的な runtime validation は導入していません。

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

- 中置演算は `a + b` のように使用できます。`*`・`/` は `+`・`-` より優先され、同じ優先順位では左から結合します。括弧で評価順序を指定できます。
- `(x)` や `(f())` は通常の括弧式として使用できます。
- 単項マイナスは `(-expr)` の形式で使用できます。括弧なしの `-expr` は使用できません。`1 - (-2)` は有効ですが、`1--2` や `1 - -2` は無効です。
- 単項マイナスのオペランドに別の単項マイナスを直接指定する構文は提供しません。`(-(-x))` や `(-((-x)))` は無効です。二重否定には `neg(neg(x))` を使用してください。
- 既存の `neg(expr)` も引き続き使用できます。`(-expr)` と型・評価・failure の扱いは同じです。標準では Int / Int64 / Float に対応します。
- `neg(expr)` と `(-expr)` は実引数から型を推論します。`print(neg(1))` と `print((-1))` はどちらも `-1` を出力します。裸の単項マイナスは引き続き禁止です。
- 除算は標準では Float 引数を要求します。`div(1, 2)` は両引数を Float に変換して実行します。
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
- `Thunk[Int, Never]` と `Thunk[Float, Never]` は異なる型として照合します。
- 引数名は照合に使用しません。sig は引数型のみを持ちます。
- 型変数名の読み替えは行いません。`T` と `U` は同一視しません。
- 呼び出しは位置引数で、値も位置順に束縛します。同名 sig のオーバーロードはありません。
- return の型は sig の戻り値と照合します（Thunkの潜在failureは上限包含を許可）。return がなければ Unit を要求します。

型変数は現在、原則として1文字の大文字で表します。generic 呼び出しは **sig の仮引数 TypeRef と実引数の静的 TypeRef のみ**から call site ごとに局所推論します。外側の assignment / argument / return の expected type や、func 本体から型変数を逆算しません。

同じ型変数への evidence は、実際に観測した型の中から、すべてを安全な widening で受け入れられる一意の型へ統合します。Int だけなら Int のままです。

| 同じ T への実引数型 | 推論結果 |
|---|---|
| Int, Int | Int |
| Int, Float（逆順も同じ） | Float |
| Int, Int64（逆順も同じ） | Int64 |
| Int64, Float | 統合不能として拒否 |

根拠がない型変数は `cannot determine type variable`、統合不能は `cannot reconcile`、候補が複数残る場合は ambiguity として拒否します。`sig make() -> T` / `sig strange(Int) -> T` は、`var x: Int = make()` / `strange(1)` でも T を決定できません。

TypeRef は再帰的に照合・置換するため、`Thunk[T, Never]` と `Thunk[Int, Never]` から T = Int を得られます。`SomeType[T]` のような既存 TypeRef 構造も同様ですが、型コンストラクタの不一致は拒否します。置換後の引数全体を既存 compatibility で検査するため、Thunk の結果型を含む型引数の variance は追加しません。latent failure 集合は具体的な既存契約として検査し、failure 型変数や集合の推論・統合は導入しません。同じ裸の T に異なる latent failure を持つ Thunk 型が渡された場合も、集合の共通上限を新たに推論しません。

型変数決定後に guarantee / typegroup の要件を検査します。`add(1,2i64)` は T = Int64 まで解決した後、Int64 が Addable を持たないため拒否します。Int64 算術は未解禁です。

型検査は呼び出しごとに `Symbols.resolved_calls` へ型変数 binding、置換済み parameter / return TypeRef、選択実装を保存します。runtime は actual Ginger TypeRef と解決済み parameter TypeRef に従って call boundary widening を行い、保存された実装を呼びます。effect 判定も同じ選択実装を使い、Python int から Int / Int64 や T を推測しません。

call expression 自体の型は置換済み return TypeRef です。外側の expected type はその後の compatibility 検査にのみ使います。

| 呼び出しと格納先 | call の型・実装・effect |
|---|---|
| `var x: Float = add(1,2)` | call は Int / core.int.add / IntegerOverflow。格納境界で Float に変換 |
| `var x: Float = add(1,2.0)` | call は Float / core.float.add / IntegerOverflow なし。第一引数を Float に変換 |
| `var x: Int = add(1,2.0)` | call は Float。Float → Int は拒否 |
| `var x: Int64 = identity(1)` | identity call は Int。格納境界で Int64 に変換 |

`print(add(1,2))` は `3`（未処理 IntegerOverflow 警告あり）、`print(add(1,2.0))` は `3.0`（警告なし）を出力します。Float 演算の引数式自体が failure を持つ場合は、その effect は従来どおり合算します。

user function の `identity(T) -> T`、`first(T,T) -> T` も同じ推論を使います。`first(1,2.0)` は両 parameter を Float として束縛します。nested call は内側を解決した静的型を外側の evidence に使います。generic 関数フレームは call site で解決した binding を保持し、本体の symbolic TypeRef を置換します（本体からの再推論はしません）。Thunk の環境スナップショットにもこの binding を保持します。generic 本体内で型変数の guarantee を使う演算は既存の検証上の制限が残ります。sig / func の厳密な構造照合、型変数名の一致条件は変更しません。

関数本体では型注釈付きの `let name: Type = expr` / `var name: Type = expr` と再代入を使用できます。`let` と parameter は再代入不可、`var` は再代入可能です。宣言型は固定し、トップレベルと同じ安全な `Int → Float` / `Int → Int64` widening を初期化・再代入の境界で適用します。逆方向や `Int64 → Float` は許可しません。型注釈省略の `let x = 1` / `var x = 1` は未対応です。

関数本体全体がひとつのローカルスコープです。引数とローカル変数は同じ名前空間に属し、引数名や宣言済みローカル名の再宣言は禁止します。宣言は順番に処理し、initializer の型検査・評価が成功してから binding を追加します。宣言前参照や `var x: Int = x` は拒否します。呼び出しごとにローカル環境を作り、関数外や次の呼び出しへ binding は漏れません。

同名のグローバル変数をローカル変数で shadow できますが、グローバル binding 自体は変更しません。既存の名前解決に従い、関数からのグローバル変数の読み取り・代入は型検査で拒否します。そのため同名グローバルがあっても `var x: Int = x` は拒否します。nested block scope・関数内 try/catch・nested function・一般的な closure は追加していません。

ローカル宣言では initializer の call を実引数から推論した後、宣言型との compatibility を検査します。次の例は `999` を出力します。initializer / assignment の failure は既存の関数 failure 上限検査へ合算します。initializer が宣言済み failure で失敗した変数は値を持たない未初期化 binding とし、再代入が失敗した場合は以前の値を維持します。

```ginger
sig increment(Int) -> Int {
    failure IntegerOverflow
}
func increment(x: Int) {
    var y: Int = add(x, 1)
    return y
}
try print(increment(9007199254740991))
catch IntegerOverflow print(999)
```

`failure Never` に置き換えると IntegerOverflow の上限違反です。`return add(x,1)` に宣言戻り値型を渡す推論は行いません。型変数は sig と実引数からの call-site 推論で解決します。ローカルにも `Thunk[Int, Never]` などを保存でき、latent failure・force・作成時の環境スナップショットは既存の Thunk 規則を維持します。

## guarantee と標準 catalog

`guarantee` はメソッドを宣言し、`impl` は型・保証・メソッドから builtin への対応を登録します。現在検証するのは、保証の存在、必要メソッドの存在、builtin 名の存在などです。処理の意味や builtin の実際の型契約までは検証していません。

`require T guarantees G` は型の保証登録を、`require T in Group` は typegroup への所属を検査します。メソッドを持つ guarantee には `impl` が必要で、`register` はメソッドを持たない保証に使います。

`ginger/catalog/` の `math`・`cast`・`ordering`・`io`・`lazy` は `core/prelude.py` から自動で読み込みます。JSON の宣言は AST に変換され、ソースの宣言とともにシンボルを構築します。

実行時は `thunk` / `force` を特別扱いし、通常の呼び出しは同名の `func`、sig 直結の builtin、guarantee 経由の impl の順に処理します。最後の経路は保証1個に対応する解決済み型変数から選んだ実装を使います。先頭引数の元の型や Python 実行時型からは選びません。

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

通常ユーザー関数では、到達可能なローカル宣言の initializer、再代入の右辺、return 式、式文の failure を合算し、未宣言 failure の伝播を型検査で拒否します。通常の呼び出しは callee の sig と引数式の failure を合算します。標準 Int 算術では、上記の具体的な impl の IntegerOverflow 契約も合算します。最初の無条件 return より後は failure 集計から除外しますが、既存の型検査は続けます。関数内 try/catch は未対応です。handled による上限検証の保留は廃止しました。

標準 `div` は `DivideByZero` を宣言しています。catch せずにトップレベルで使うと未処理警告の対象になります。値に応じた failure の絞り込みは行わないため、除数がゼロでない呼び出しも警告対象です。

```ginger
try print(div(1.0, 0.0))
catch DivideByZero print(0)
```

この例は警告なしで `0` を出力します。

現在採用している catch の規則は、try 対象式から静的に発生し得ると宣言・推論された failure だけを捕捉対象として認めることです。すべての catch を、捕捉分を削除する前の元の try 集合に対して検査します。集合外の既知 failure、未知の failure 名、Never は catch できません。実行時にたまたま未宣言の `RaisedFailure` が発生しても、静的集合外の catch を許可する根拠にはしません。

- `try` 文の対象式は任意の結果型を許可します。正常終了時の結果値は破棄され、`try` 文自体は値を生成しません。将来的な値を返す `try` 式は今回の仕様には含めません。
- `try` の後には1個以上の `catch` が必要です。catch式はUnitに限定され、関数本体内では使えません。
- トップレベルでは、捕捉・処理後に静的集合に残る failure を警告します。警告だけでは実行を止めません。これは関数本体の上限契約違反を型エラーにする規則とは別です。
- Phase 6 では try 評価中に新しく発生した各イベントについて、種類に一致する最初の catch を発生順に実行します。未宣言の `RaisedFailure` や Python 例外は catch で通常処理せず停止します。
- 同名 catch の重複は現在も許可します。
- handler 内で発生した宣言済み failure は、元の catch 対象と同名でも新しいイベントとして保持し、handler を未完遂として後続文へ進みます。同じ try の後続 catch では捕捉しません。handler が fatal 以外で終了すると元イベントを resolved にします。handler 由来の新イベントは unresolved のままです。

静的な外向き failure 集合は `(try failures - caught failures) ∪ handler failures` です。handler の failure 集合から catch 対象の failure を削除しません。重複 catch を許可する既存挙動は維持しています。

builtin の宣言は静的解析が信頼する契約であり、Python 実装から実際の failure を自動推論・検証してはいません。独自 builtin sig の宣言漏れは静的には検出できませんが、Phase 7 では実際に未宣言 RaisedFailure が出た境界で契約違反として検出します。print / IO / toFloat の failure 契約は未確定のままです。現在、標準 print の出力失敗や、独自 builtin が契約違反の巨大整数を返した場合の toFloat 変換失敗は Python 例外として伝播し、Ginger の catch が捕捉する `RaisedFailure` には変換されません。

## @attr.handled の廃止（failure runtime 再設計 Phase 1）

`@attr.handled` は廃止しました。sig・func・builtin sig・catalog JSON の属性として指定すると、未知の属性として明示的にエラーになります。旧コードは属性を削除し、通常の failure 宣言と必要に応じた catch へ移行してください。

failure の種類を指定しない runtime の握りつぶし、呼び出し先の静的 failure の除去、自身・直接依存・間接依存を理由とする上限契約検証の保留は行いません。`FAILURE_CONTRACT_DEFERRED` の note も生成しません。builtin の宣言を信頼する既存の検証境界は引き続き存在します。

汎用の `@attr.<name>` 構文は維持し、`@attr.io` は引き続き使用できます。属性は sig / func の前にのみ置けます。不正な位置の属性は黙って破棄せず、構文エラーにします。

Phase 1 時点では failure 履歴や statement 単位の継続は未導入でした。未捕捉の `RaisedFailure` は従来どおり伝播して実行を停止します。将来の runtime 再設計とは段階を分け、`@suppress` も導入していません。

## 内部 runtime の骨格（Phase 2）

`ginger/runtime/failures.py` に `FailureEvent` / `FailureStatus`、`context.py` に `RuntimeContext` / `CallFrame`、`results.py` に `Value` / `NoValue` / `EvalResult` / `ExecutionResult` を追加しました。静的契約は FailureId の集合、実行時履歴は発生ごとに異なる event ID を持つ記録として分離します。

RuntimeContext 内で event / call ID を採番します。イベントとフレームは不変のスナップショットで、context の操作が同じ ID の記録を更新します。更新後の状態は context から再取得します。resolved にしても履歴から削除しません。pending はイベント ID の参照であり、関数境界の自動伝播は未実装です。

`Value(None)` は正常な Unit、`NoValue()` は値の欠落です。ExecutionResult は環境の対応表と履歴のスナップショットを保持しますが、環境内の値そのものを深くコピーしません。未解決イベントは保持した履歴から取得します。

Phase 2 時点では単体で使用できる内部構造のみを導入しました。Phase 3 の接続範囲は以下のとおりです。

## builtin failure bridge（Phase 3）

`ginger/runtime/builtin_bridge.py` を実際の builtin 呼び出し境界へ接続しました。正常終了は `EvalResult(Value(v))`、builtin 自身の宣言済み `RaisedFailure` はイベント登録と `EvalResult(NoValue(), related_event_ids)` に変換します。静的解析と runtime は `builtin_failure_contract` を共有し、既存の sig failure と標準整数演算の実装別契約を参照します。独自の直接 builtin sig は自身の宣言を使用し、実装名だけで契約を補いません。

引数評価は bridge の外で先に行い、引数の failure を外側 builtin の契約で判定・再登録しません。origin は `core.int.add` などの実装 ID です。実行ごとに内部 RuntimeContext と `<program>` の root call を作り、ユーザー関数内の builtin にも暫定的に同じ root call ID を使用します。これは Phase 3 時点の制限で、Phase 5 で関数別 CallFrame を導入しました。

Phase 3 時点では互換境界 `legacy_value` が NoValue を RaisedFailure へ戻し、既存停止挙動を維持していました。この互換境界は Phase 4 で撤去しました。

Phase 3 時点の未宣言 RaisedFailure の再送出は、Phase 7 で FailureContractViolation に置き換えました。通常イベントとしては登録しません。Python の実装エラーはイベント化しません。ただし、既存 evaluator の ZeroDivisionError → DivideByZero 変換は bridge 内に移して維持しています。内部テストでは `_eval_program_with_context` に context を渡して通常と同じ経路を観測できます。Phase 8 で pipeline から ExecutionResult と履歴を公開しました。

## statement 単位の継続（Phase 4）

宣言済み builtin failure は `FailureEvent + NoValue` として式の親へ返します。引数は左から右に評価し、NoValue に達したら残りの引数と呼び出し先を実行しません。値を必要とする親演算も実行せず、原因 event ID を保持したまま文を未完遂にし、次の文へ進みます。実行済みの副作用は取り消しません。

```ginger
print(div(1.0, 0.0))
print(3)
```

この例は静的警告に続いて `3` を出力します。最初の print は呼び出されず、DivideByZero のイベントは unresolved のまま残ります。同じ failure の再発は別イベントです。`RuntimeContext.incomplete_statements` で root call ID、scope、文の index / 種類、原因 event ID を内部的に確認できます。index は program items または関数 body 内の0始まりで、source locationではありません。

初期化失敗には `UninitializedBinding` を使用します。型・可変性・原因 event ID のみを保持し、None・0・NoValue を変数の値として保存しません。参照すると同じ原因の NoValue を返し、新しい failure を生成せず、その依存文も未完遂にします。未初期化 var は後の正常代入で回復できます。既存値のある変数の再代入が失敗した場合は既存値を維持します。未宣言変数・型エラー・未宣言 RaisedFailure・Python 内部例外などの致命的エラーでは継続しません。

Phase 4 では関数本体の通常文にも継続を適用しました。以下の root のみという制限と Unit 終了の扱いは Phase 5 で更新しています。値欠落は呼び出し側へ返せますが、関数ごとの pending 管理・成功値と一緒に未解決イベントを受け渡す契約は Phase 5 です。正常な return は値を返し、途中の failure 履歴は root に残ります。return 式が NoValue なら、その関数本体を終了して呼び出し側へ NoValue を返します。Phase 9 でこの終了規則とreturn以降の静的到達不能を正式仕様として確定しました。明示 return なしで未完遂文を含む本体は NoValue、すべて完遂した本体は正常な Unit を返します。

try-catch は暫定的に NoValue の先頭原因の種類でhandlerを選び、以前から保持している他のイベント全体は検索しません。handler由来NoValueを後続catchへ渡さず、後続トップレベル文へ進みます。resolved化・複数イベントごとのcatch処理はまだ行いません。正常値と pending を持つ関数呼び出しの暫定対応は Phase 5 に記載します。

Thunk / force は EvalResult の受け渡しに必要な対応のみ行い、遅延評価・snapshot の既存規則は維持します。潜在契約のruntime照合は Phase 9 で追加しました。Phase 4 時点の公開 API は環境辞書でした。Phase 7 で契約違反、Phase 8 で ExecutionResult と履歴公開・main診断を導入しました。未初期化bindingも結果環境に保持します。

## 関数呼び出しごとの pending 伝播（Phase 5）

実行開始時の `<program>` に加え、ユーザー関数を呼ぶたびに一意の CallFrame を作成します。parent_call_id は呼び出し元、declared_failure_contract は型検査と同じ Symbols.sig_failures の集合です。実際に発生したイベントの pending_event_ids とは別に保持し、この段階では関数境界の runtime 契約違反判定を追加しません。

RuntimeContext.current_call_id を scope で切り替え、正常終了・NoValue・fatal 例外のいずれでも親へ復元します。終了したframeの履歴は削除しません。builtinイベントは発生時のcurrent call IDと実装originを保持し、callee終了時は未解決イベントIDだけをcallerへ渡します。イベントは作り直さず、発生元call IDも変更しません。resolvedイベントは履歴に残し、親pendingには追加しません。fatal終了時も、それ以前に発生した未解決イベントの参照は親へ伝播します。

CallFrame.pendingが実行時の正本です。関数のEvalResultには生成された値（またはNoValue）と関連イベントIDを添えます。正常値はpendingがあっても利用でき、値を得たことだけでfailureをresolvedにしません。関数末尾への到達は正常Unit `Value(None)` であり、途中の未完遂文によるpendingがあってもNoValueにはしません。失敗したreturnはPhase 4同様、関数を終了してNoValueを返します。callerはその依存文を未完遂とし、独立した次の文へ進みます。

try-catchは関連イベントの先頭原因に一致する最初のhandlerを選ぶ暫定処理です。正常値やUnitとpendingを返す関数にもこの選択を適用して、従来のhandler実行を可能な範囲で維持します。この暫定処理は Phase 6 のイベント単位処理で置き換えました。incomplete statementには実際のユーザー関数call IDが記録されます。

同関数の反復呼び出し・再帰も呼び出し単位で区別します。テストの再帰は条件分岐のない現構文に合わせ、host builtinが3回目でfatalエラーを出す有限の実行でframe分離とcleanupを検証しています。FailureContractViolation導入はPhase 7、公開ExecutionResultはPhase 8、returnの終了規則とThunk契約の統合はPhase 9で確定しました。

## イベント単位の try-catch（Phase 6）

try 開始時の次の event ID を境界として記録し、try 式評価が終わった時点で、その期間に新しく登録されたイベントIDを履歴順に固定します。評価結果の Value / NoValue や関連IDの先頭だけで判断しません。try 以前の同種イベントや、未初期化変数の参照で再利用した古い原因イベントは対象外です。

各 unresolved イベントについて、種類が一致する最初のhandlerを実行します。同種failureが2回発生すればhandlerも2回実行します。未対応の種類は未解決のまま残り、実行を継続します。handlerが正常終了または宣言済みfailureによるNoValueで終了した場合に限り、対象イベントをresolvedにします。fatalな未宣言RaisedFailure・Python例外・EvalErrorでは停止し、対象はunresolvedのままです。

handlerが起こしたfailureは新しいIDのunresolvedイベントとして残ります。元イベントを再利用せず、`caused_by`に処理対象のevent IDを記録します。処理対象リストは固定済みなので、同じtryの後続catchではhandler由来イベントを捕捉しません。handlerのNoValueはCatchStmtとして既存のincomplete記録にも残します。

resolvedイベントも履歴に残ります。CallFrame.pending_event_idsの参照も伝播履歴として保持し、現在有効なpendingは `RuntimeContext.unresolved_pending` でstatusを照合して取得します。callee終了時もunresolvedだけをcallerへ渡します。関数内try-catch構文は未対応のため、この境界は共通runtime helperのテストで確認しています。

静的集合 `(F(try) - caught) ∪ F(handlers)` は変更していません。tryは引き続きstatementで、式として値を返す新構文はありません。評価中に正常値が生成されればその値の利用・副作用は維持し、failure処理だけを独立して行います。tryネスト・関数内try構文は未実装です。失敗return後のcallee内継続は採用せず、Phase 9で関数終了を正式仕様としました。公開ExecutionResult APIはPhase 8で導入しました。FailureContractViolation は Phase 7 で導入しました。

## Thunk と遅延評価

```ginger
var x: Int = 1
var t: Thunk[Int, Never] = thunk(x)
x = 2
print(force(t))
print(x)
```

現在の出力は `1`、`2` です。

`thunk(expr)` は型検査後、実行時には式を評価せず保存します。`force(t)` は呼ぶたびに保存した式を再評価し、結果をキャッシュしません。どちらも引数は1個です。force の戻り値は期待型がある文脈では型照合します。

現在は環境辞書を浅くコピーし、外側の環境は参照で保持します。トップレベルの再代入は Cell を置き換えるため、上の例では Thunk が作成時の値を読みます。これはすべての値を深くコピーするという保証ではありません。

正式な型形式は `Thunk[結果型, failure指定...]` です。

```ginger
failureset CalculationFailure { DivideByZero IOErr }
// 型の例: Thunk[Int, Never], Thunk[Int, DivideByZero], Thunk[Int, CalculationFailure]
var t: Thunk[Float, CalculationFailure] = thunk(div(1.0, 0.0))
try force(t)
catch DivideByZero print(0)
catch IOErr print(1)
```

Thunkのfailure指定はforce時に発生しうるfailureの上限契約であり、Thunk作成時のfailureではありません。
`thunk(expr)` は結果型とexprのfailure集合を保存し、作成式のcurrent failureは空です。
`force(t)` は保存された契約をcurrent failureへ復元します。引数を評価するfailureも合算し、
`print(force(t))` にも通常の引数effect伝播で届きます。`force(thunk(expr))` も同じ経路です。
`try force(t)` も通常の `try` 文の規則に従い、対象式は任意の結果型を許可します。正常終了時の結果値は破棄され、`try` 文自体は値を生成しません。値を返す `try` 式は今回の仕様には含めません。catch式は引き続きUnitです。

failure指定には個別FailureId、failureset、単独のNeverを使えます。Neverは空集合です。
指定省略、未知名、Neverとの混在、同じ指定名の重複は拒否します。failureset展開後の重なりは統合します。
代入・関数引数・戻り値では、結果型の完全一致とactualの潜在failure集合がdeclaredの部分集合であることを検査します。
sig/funcの引数宣言は展開後の集合も含めて再帰的に完全一致させます。一般的な部分型や結果型のvarianceは導入していません。

Thunkを返す関数の自身のfailureと、返したThunkの潜在failureは別契約です。
`sig make() -> Thunk[Float, DivideByZero] { failure Never }` は有効です。
静的に解決されたThunkは本体上限検証の対象です。
静的契約はruntimeオブジェクトへ追加していません。catalogのthunk/forceのTはintrinsicのプレースホルダーで、
実際の型・failureは専用規則で計算します。通常のcatalog Thunk型では`args`に結果型1個、`failures`に指定名配列を記述します。

## 現在の制限・未確定事項

| 項目 | 制限・判断が必要な点 |
|---|---|
| 型変数推論 | sig 仮引数と実引数から call site ごとに推論。外側 expected type / 関数本体からの逆推論はしない |
| 型引数・ジェネリクス | TypeRef の再帰照合・置換に対応。一般的な variance、failure 型変数、overload、alpha-equivalence は未対応 |
| guarantee の契約 | 型変数の保証を本体内の呼び出し検査に十分反映できない。複数保証やメソッドの型契約と実装選択の関係も未確定 |
| failure の契約 | 通常関数の上限検証と catch の静的集合制限は導入済み。builtin 実装の自動検証はなく、print / IO / toFloat の契約は未確定。未処理は警告、重複 catch は許可。handler の failure は同名でも外へ伝播 |
| handled | 廃止済み。sig / func / builtin sig で指定するとエラー |
| Thunk | 明示的な潜在failure上限契約を保持し、forceで復元する。本体上限契約を検証 |
| Catalog とソース | 役割の強制や複数ファイルの読み込みはない |
| 名前付き引数 | 構文解析は対応するが、通常の型検査・実行では拒否 |
| スコープ | 関数外変数への参照は通常の型検査で拒否。評価器には外側環境を参照する処理が残る |
| 関数内ローカル変数 | 型注釈付き let / var と var 再代入に対応。型注釈省略・nested block scope・関数内 try/catch は未対応 |
| CLI | main がサンプル固定。入力パス指定の CLI は未実装 |
| String・制御構文など | 文字列リテラル、条件分岐、ループ、ユーザー定義データ構造は未実装。String の実行時処理や builtin は一部存在する |

未実装項目は、今後追加すべき必須機能を意味しません。対象に含めるかどうかも含め、別途判断が必要です。

## テスト

failure 契約の自動回帰テストは `tests/test_failure_contract.py` にあります。上限契約、catch 資格、宣言正規化、handled の拒否・Thunk / builtin の契約と、既存の型検査・サンプルの回帰確認を担います。

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
| `ginger/scripts/` | 現行の動作例と過去の試行サンプル |
| `tests/test_failure_contract.py` | failure 契約・検証境界・既存動作の自動回帰テスト |

## 開発上の注意

この README は現時点の実装状況の記録です。未確定事項はコードや README のどちらかを正と決めつけず、設計判断を先に行います。

古い `catalog` / `fn` / `args:` 構文、`run_ginger.py` による起動、Catalog / Code の自動分離は現行の使い方ではありません。`scripts/` の古いサンプルやコメントにも不一致があります。既存ファイルの存在だけを、対応機能や互換性の保証とは扱わないでください。

### failureset（第1段階）

既存の FailureId 集合に名前を付け、sig の failure 宣言で参照できます。

```ginger
failureset CalculationFailure {
    DivideByZero
    IOErr
}
sig calculate() -> Float {
    failure CalculationFailure
}
func calculate() {
    return div(1.0, 2.0)
}
```

symbols 構築時に名前を個別の FailureId 集合へ展開し、既存の failure
契約検証を適用します。通常の failure 宣言と併用でき、展開後の重なりは集合の和に
なります。同じ名前を sig に繰り返し記述する既存の重複エラーは維持します。
定義位置にかかわらず参照でき、catalog JSON の既存の `failures` 配列からも、
同じプログラム内の source failureset を参照できます。catalog 単独での解決には
その定義が必要です。新しい catalog JSON 形式は追加していません。

要素は直接の既知 FailureId に限定します。未知の要素、要素の重複、`Never`、
集合名の重複、ネスト（自己参照を含む）はエラーです。空の定義は現段階では未対応として
拒否します。FailureId および `Never` と同名の集合は曖昧になるため禁止し、
type・guarantee・typegroup・関数とは既存の別々の名前表に合わせて同名を許容します。

集合名自体は FailureId ではなく、catch には使用できません。catch の対象は従来どおり
try 対象から静的に発生しうる個別の FailureId です。集合演算は未導入です。`@attr.handled` は上記 Phase 1 で廃止しました。

## runtime failure 契約検証（Phase 7）

宣言済みfailureは引き続きイベントとして保持し、statement単位で継続できます。unresolvedであること自体は契約違反ではなく、root programは未処理イベントを保持できます。

builtinは引数評価後、実装自身が送出したRaisedFailureだけを、選択された実装の契約と照合します。静的解析と同じ `builtin_failure_contract` を使用します。直接builtin aliasやcustom builtinも自身のsig契約で判定し、callerの宣言で救済しません。未宣言failureは通常イベントを作らず、fatalな `FailureContractViolation` として停止します。Python内部例外はこの型へ変換しません（従来のZeroDivisionError→DivideByZero変換は維持）。

ユーザー関数は戻り値をcallerへ渡す直前に、CallFrameのunresolved pendingを自身のsig契約と照合します。callee由来イベントも対象ですが、resolved履歴は対象外です。正常値・Unit・NoValueのいずれでも同じ検証を行います。合法なイベントは同じIDのまま伝播します。違反時は既存履歴をunresolvedのまま残し、通常のpending伝播を停止します。current frameは例外経路でも復元します。

Violationは通常catchの対象ではありません。handler中の違反では元のcatch対象はunresolvedのままです。診断にはfailure、origin、違反境界名、call ID、宣言集合、存在する場合event IDを含めます。内部RuntimeContextの `last_contract_violation` で参照できます。公開ExecutionResultとmain表示への統合はPhase 8です。

forceで実際に評価されたbuiltinとユーザー関数には同じ境界検証が適用されます。Thunkの潜在契約照合とforce時のcall帰属はPhase 9で統合しました。returnはNoValueでも関数を終了します。

## 公開実行結果（Phase 8）

`ginger.pipeline.run(source)`、`execute(program)`、`ginger.eval.eval_program(program)` は正式な実行結果 `ExecutionResult` を返します。旧環境辞書の参照 `eval_program(program)[name]` は `eval_program(program).environment[name]` へ移行してください。dict互換の振る舞いは追加していません。

```python
from ginger.pipeline import run

result = run("var x: Int = 3")
print(result.environment["x"].value)
for event in result.unresolved_events:
    frame = result.call_frames[event.call_id]
    print(event.event_id, event.origin, frame.function_name)
if result.contract_violation is not None:
    print(result.contract_violation)
```

取得可能な情報:

- `environment`: 正常終了または契約違反で停止した時点のトップレベル環境。確定済み値と未初期化bindingを保持し、失敗した値を捏造しません。関数ローカル環境は含みません。
- `failure_history`: resolvedを含む全FailureEventの発生順tuple。event ID、発生call ID、caused_byをそのまま保持します。
- `unresolved_events`: 履歴のstatusから毎回導出するtuple。同種failureも別イベントです。
- `call_frames`: call IDからCallFrameを取得できる読み取り専用mapping。終了済みframeとparent関係も保持します。
- `incomplete_statements`: call ID、scope、statement index/kind、原因event IDを持つ既存記録のtuple。
- `contract_violation`: 違反がなければNone。違反時は `ContractViolationSnapshot` にfailure ID、origin、違反call ID、境界名・種別、宣言契約、任意のevent IDを保持します。

RuntimeContextは実行中の状態、ExecutionResultは終了時点のsnapshotです。履歴と未完遂記録は不変レコードのtuple、frame対応表はコピーした読み取り専用mapping、違反情報は例外から切り離したfrozen dataclassです。環境対応表もコピーしますが、bindingやGinger値はdeep copyしません。

unresolvedだけなら正常に完走して結果を返します。契約違反はGingerプログラムを即停止しますが、公開APIは停止時点の結果を返します。ユーザー関数境界の違反では原因eventを履歴に残し、builtin未宣言failureではeventを作りません。違反で伝播が止まったeventはroot pendingに未到達でも `unresolved_events` に含まれます。正常完走時もraw pendingにはresolved参照が残り得るため、現在の未解決状態はevent.statusで判定します。

Python内部例外、parse/typecheckエラーは結果へ隠さず従来の例外として送出します。内部 `_eval_program_with_context` は評価・テスト用の例外経路を維持します。コンパイル診断はruntime結果へ統合しません。

mainはunresolvedイベントを1件ずつstderrへ表示し、failure名・event ID・origin・call IDを含めます。resolved履歴は自動表示しません。契約違反もstderrへ専用診断を出し終了コード1、正常完走はunresolvedの有無によらず0です。既存の静的警告もstderrへ移しました。Python内部例外は隠しません。

Phase 9でreturnの既存終了規則を確定し、Thunk/forceの潜在契約・call帰属を統合しました。

## returnと遅延評価（Phase 9）

`return expr` に到達すると、Value/NoValueにかかわらず現在の関数を終了します。NoValueでも後続文や第二returnは実行せず、静的解析のreturn以降到達不能という規則を維持します。NoValueは原因event IDとともにcallerへ返り、callerの依存statementを未完遂にした後、次の独立statementで継続します。関数終了時のfailure契約検証は必ず適用します。

正常値とpending failureは共存し、正常Unit `Value(None)` とNoValueは別です。未初期化bindingから別変数やprintへ値欠落が伝わっても同じ原因event IDを使い、新eventは生成しません。

Thunkは非memoizedです。作成時には式を実行せず、FailureEventも生成しません。作成時のlexical environmentと、型検査で推論した潜在failure契約を保持します。forceごとに再評価するため、実際に同じfailureが再発すれば別eventになります。既存の環境capture方式は変更していません。

force時は通常のEvalResult経路を使います。NoValueなら親演算を実行せず、正常値とpendingが共存するなら両方を保持します。builtinとユーザー関数の契約検証を通した後、今回のforce中に新しく生じたunresolved eventを、Thunkが保持する推論契約およびforce引数の公開Thunk型の潜在契約に照合します。契約外なら境界種別 `thunk`、境界名 `force` のFailureContractViolationとして停止し、既存eventを履歴へ残します。

捕捉した未初期化bindingが参照する過去の原因eventは、forceが新しく発生させたfailureではありません。識別子参照の静的effectは空であるため、これをThunkの新しい潜在effectとして再判定せず、元のNoValue因果参照として伝えます。

force専用CallFrameは追加していません。builtin failureはforce実行中のcurrent callへ帰属し、Thunk内のユーザー関数呼び出しは通常のchild frameを作ります。lexical environmentを作成元から取得してもcall identityを作成元へ戻しません。

force中に生成されたeventもtryの対象となり、handler内forceの新eventには通常どおりcaused_byが付きます。同じtryで再catchしません。Python内部例外は通常の例外として表面化し、Violationは既存のExecutionResult.contract_violationへ、その他の履歴・frame・未完遂記録も既存フィールドへ公開します。新しい公開結果フィールドやfailure構文は追加していません。
