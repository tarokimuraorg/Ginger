# Ginger

Ginger は、Python で実装された独自のプログラミング言語処理系です。AI と人間が設計意図を共有し、関数の宣言・実装・失敗の可能性を記録することを目指しています。

現在は開発途中で、仕様も一部未確定です。この README は現時点の実装状況を記録したもので、すべての挙動を将来も維持する正式仕様ではありません。

## 実行方法

プロジェクトルートで、Python 3.10 以降を使って実行します。現行コードは Python 標準ライブラリを使用しています。

```sh
python3 -B -m ginger.main
```

現在の `ginger/main.py` は `ginger/scripts/Scene_3.ginger` を固定で読み込み、標準出力へ `-1`、`0.0` の順に出力します。DivideByZero が1件発生しますが、resolve により resolved となり履歴に残ります。静的警告・未解決イベントの表示はなく、標準エラー出力は空、終了コードは0です。入力ファイルを引数で指定する CLI はありません。`-B` は Python のバイトコードキャッシュ生成を抑止します。

別のサンプルは Python API から実行できます。次の例もプロジェクトルートで実行します。

```sh
python3 -B - <<'PY'
from pathlib import Path
from ginger.pipeline import run

run(Path("ginger/scripts/Scene_7.ginger").read_text(encoding="utf-8"))
PY
```

Scene 1〜7 は実行可能です。Scene 1 は IntegerOverflow の未処理警告が2件と DivideByZero / FloatOverflow の警告が1件、Scene 3 は FloatOverflow が1件、Scene 7 は IntegerOverflow が1件出ます。Scene 2・5・6 には未処理警告はありません。Scene 4 は現在 `1`、`2`、`123` を出力し、IntegerOverflow の静的警告が2件、実行時の未解決イベントが1件あります。上の Scene 7 の例は、標準出力へ `-2`、`-2`、`3` の順に出力します。警告は標準エラー出力へ `warning[UNHANDLED_FAILURES]: unhandled failures: IntegerOverflow` と表示され、実際の failure は発生しません。

## 処理フロー

`ginger/pipeline.py` が処理をまとめています。

```text
ソース → parse → lower → typecheck → eval
```

1. `parse`: 字句解析・構文解析を行い、AST（構文木）を生成します。
2. `lower`: 中置演算子式を `add`・`sub`・`mul`・`div`、括弧付き単項マイナスを `neg` の呼び出しへ変換します。
3. `typecheck`: 標準 JSON catalog とソースの宣言を基に、型・宣言の対応・制約、通常ユーザー関数の failure 上限契約、binding initializer に基づく resolve 資格を検査し、後続 resolve を反映したトップレベルの未処理 failure を警告します。builtin の実装契約には後述の検証境界があります。
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
| `typegroup` | concrete type identity の集合と `require T in Group` による所属検査 |
| `failure` / `resolve` | 無条件・guarantee 条件付きの failure 上限契約、initializer の具体的 failure 集合に基づく資格検査、未処理警告。resolve はトップレベルの var のみ |
| `Thunk` / `thunk` / `force` | 式の遅延評価と強制評価 |
| 標準 JSON catalog | 数値演算・変換・比較・出力・遅延評価の宣言を自動注入 |

## 型・変数・式

通常の整数リテラル `123` は Int、suffix を直接付けた `123i64` は Int64 です。`1.0i64` は無効です。範囲外リテラルは静的に拒否します。既存の leading zero の許容は維持します。

| 型 | 範囲 |
|---|---|
| Int | `-(2^53 - 1)` ～ `+(2^53 - 1)`（±9,007,199,254,740,991） |
| Int64 | `-(2^63 - 1)` ～ `+(2^63 - 1)`（±9,223,372,036,854,775,807） |
| Float | finite-only binary64。exact Python float かつ `math.isfinite(value)` |

Int は、すべての有効な値を binary64 Float へ整数精度を失わず変換できる安全整数型です。Int64 は通常の signed 64-bit と異なり `-2^63` を含まない対称範囲で、最小値の negation も最大値に収まります。負数は `(-1)` / `(-1i64)` と書き、裸の `-1` は引き続き無効です。

| 暗黙変換 | 許可 |
|---|---|
| Int → Float | OK（有効な Int は精度損失・値域 overflow なし） |
| Int → Int64 | OK |
| Int64 → Float / Int | NG（小さい値でも禁止） |
| Float → Int / Int64 | NG |

トップレベル変数には明示的な型注釈が必要です。widening は変数初期化・再代入・具体型の関数引数・return の使用境界で行います。`var x: Int = 1` から `var y: Int64 = x`、`var z: Float = x` に変換しても、元の x の宣言型は Int のままです。runtime でも宣言型を保持し、Python int から Int / Int64 を推測しません。

3型は別の型です。sig / func の構造照合は厳密なままで、`Thunk[Int]<Never> → Thunk[Int64]<Never>` などの generic variance は導入しません。同じ型変数に対応する実引数は、以下の call-site 推論で安全な widening により統合できます。`toFloat` は引き続き `Int → Float` 専用です。

Int64 に提供する capability は `Negatable`・`Printable`・`Ord`（neg、print、cmp / eq / lt）のみです。add / sub / mul / div は未対応です。

標準 Int の **add / sub / mul は結果を範囲検査**し、Int 範囲外なら `RaisedFailure(FailureId.IntegerOverflow)` を送出します。範囲外の結果を変数や関数の戻り値へ渡しません。Int64 への自動昇格・wraparound・clamp・saturation は行いません。戻り値型は常に Int です。neg は対称範囲内に収まるため IntegerOverflow を持ちません。

範囲外リテラル（例: `9007199254740992`、`9223372036854775808i64`）は従来どおり静的エラーです。有効な入力から演算した結果の超過だけが runtime の IntegerOverflow になります。Int → Float / Int64 widening 自体に failure は追加しません。`toFloat` にも FloatOverflow はありません。

Float の正常値は `type(value) is float and math.isfinite(value)` です。NaN / +inf / -inf は正常な Float ではありません。±0.0、subnormal、丸められた有限値、underflow による ±0.0 は有効です。decimal literal を parser が Python float に変換して inf になった場合は、typecheck の FloatLit 検査で静的に拒否します。負方向の `(-literal)` も同様です。nan / inf / infinity の literal 構文は追加していません。

標準 Float の add / sub / mul / div は、全 operand が finite で結果が non-finite の場合に `RaisedFailure(FailureId.FloatOverflow)` を送出します。FloatOverflow は finite Float domain の overflow を表す通常 failure で、FailureEvent → NoValue を経て resolve で回復できます。div は divisor が ±0.0 なら DivideByZero を優先し、nonzero finite の除算結果が non-finite なら FloatOverflow です。finite 値の neg は常に finite なので failure を持ちません。cmp / eq / lt のアルゴリズムは変更せず、通常 source と builtin return 境界で NaN の流入を防ぎます。

**generic 演算の failure 契約:** math catalog の `add(T,T) -> T` / sub / mul は、呼び出し側へ `failure IntegerOverflow when T guarantees BoundedArithmetic` と `failure FloatOverflow when T guarantees FiniteArithmetic` を公開します。call-site で T = Int なら `{IntegerOverflow}`、T = Float なら `{FloatOverflow}` になります。両 guarantee はメソッドを持たない独立した marker です。標準登録は BoundedArithmetic が Int のみ、FiniteArithmetic が Float のみで、Int64 はどちらにも登録しません。FiniteArithmetic は標準 Float 算術が finite domain の overflow を FloatOverflow として公開する能力を示します。Addable・Subtractable・Multipliable とは独立し、marker の登録だけで算術を使用できるようにはなりません。Int64 算術は未対応のままです。

`ginger/builtin.py` の `INT_ARITHMETIC_FAILURES`・`FLOAT_ARITHMETIC_FAILURES` と `builtin_failure_contract` は、`core.int.add/sub/mul` と `core.float.add/sub/mul/div` が実際に送出する failure の runtime 検証に引き続き使用します。caller-visible な条件付き宣言と Python 実装の契約は別に保持します。条件付き宣言に表現されていない実装由来 failure は、選択された具体実装から静的 effect に合算し、generic 本体では同じ guarantee / method に登録された実装だけを保守的に合算します。式全体の effect には引数評価の failure も含むため、Float binding の initializer でも、その引数が IntegerOverflow を起こす式なら resolve の対象に指定できます。

型に基づく契約なので `add(1,2)` も IntegerOverflow の可能性を持ちます。値に基づく除外はしません。resolve 資格検査、関数の `inferred_failures ⊆ declared_failures` 検査、failureset、Thunk の latent failure に統合しています。余分な failure をユーザー関数が宣言する既存ルールは維持し、その宣言は呼び出し側でも有効です。

```ginger
var t: Thunk[Int]<IntegerOverflow> = thunk(mul(9007199254740991, 2))
var x: Int = force(t)
resolve x {
    IntegerOverflow {
        x = 1
    }
}
print(x)
```

return 式には宣言戻り値型を generic 型変数の推論用期待型として渡しません。`return add(x,1)` や `return neg(x)` は、実引数から型変数を決定できるため有効です。`make() -> T` のように引数側に T の根拠がなければ、外側の return 型が具体型でも拒否します。

整数生成経路の監査結果:

| 経路 | 保証・制約 |
|---|---|
| Int / Int64 literal | 静的範囲検査 |
| 標準 Int add / sub / mul | 範囲内の Int を返すか IntegerOverflow を送出 |
| Int / Int64 neg | 有効入力では対称範囲内。failure なし |
| その他の標準 builtin | div / toFloat は Float、cmp は Ordering、eq / lt は Bool、print は Unit。整数を新規生成する他の標準 builtin はない |
| 変数・引数・return・Thunk | 具体的な静的 TypeRef により、read / argument / write / return / force の consumer 境界でも検証 |
| 独自 builtin sig / impl | argument / return 境界で標準型の representation / 値域を検証。不正な値は ValueContractViolation |
| Python API の直接呼び出し | Cell / 外部 environment の注入などは全面検証しない |

独自 builtin sig は自身の failure 宣言を維持し、Python 実装から自動推論しません。例えば標準 `core.int.add` を直接参照する独自 sig でも、呼び出し側へ IntegerOverflow を公開する責任は宣言側にあります。標準実装 ID を参照しただけで標準の conditional contract を自動付与しません。runtime の標準算術結果検査は実行され、alias 自身の契約にない failure を送出した場合は FailureContractViolation になります。Float 実装を直接参照する alias も同様に、自身の FloatOverflow 宣言が必要です。

builtin return validation は Int / Int64 の exact Python int と範囲、Float の exact Python float と有限性、Bool、Ordering、Unit を検証します。Float の型違い（int / bool / None / float subclass など）は `wrong_python_type`、NaN / ±inf は `non_finite` reason の ValueContractViolation です。これは fatal で FailureEvent を作らず、resolve 不可、不正値を公開せず、`ExecutionResult.value_contract_violation` に記録します。未知 TypeRef、Box / Pair などの opaque 型は従来どおり通過し、String semantics も変更しません。

Phase 3C では custom builtin の `Thunk[T]<F>` return を後述の handle adapter で internal value に戻した後、Phase 2.5 の共通 `validate_runtime_value` を適用します。`isinstance(value, ThunkValue)` を要求し、保存された `potential_failure_contract` が公開 TypeRef の `latent_failures` 以下であることを、正規化済み FailureSet の包含関係で検証します。外形違反は `wrong_python_type`、保存契約の超過は `contract_mismatch` の ValueContractViolation です。内部式はこの時点では評価しません。

`force` は既存の実 failure 契約検証後、Value の場合だけ具体化済み result TypeRef で同じ validator を適用します。標準6型の成功値を検査し、NaN / ±inf などを caller・assignment・後続処理へ公開する前に fatal に停止します。NoValue は検査対象外です。診断 snapshot は expected TypeRef、Python type、安全な値表現、reason、call ID と境界を保持し、force は `boundary_kind="thunk"`、`implementation="force"` で識別します。nested Thunk は force ごとに一層だけ外形と保存契約を検査し、内側の式を先行評価しません。ThunkValue は result TypeRef を保持せず、force 式の静的型と call frame の type binding による既存の型置換を正本にします。generic T も Python 値から推測しません。Box / Pair / MissingType などを result に持つ Thunk も外形と保存契約は検査しますが、opaque payload の representation は検査しません。

Phase 3A では runtime value contract を producer / consumer 双方で検証します。共通の `prepare_typed_value` は source TypeRef の検証 → widening → target TypeRef の検証を行います。`Int -> Float` / `Int -> Int64` の変換規則は変更せず、範囲外 Int や Bool-as-Int が変換により正常値になる前に停止します。`numeric.widen_value` は pure conversion helper のままです。

checked identifier は expression の静的 TypeRef で読み出しを検証します。builtin / custom builtin / user function は全 argument の準備後にだけ実行し、parameter Cell に不正値を保存しません。引数式の評価順は従来どおり左から右です。user function の正常 return、typed Cell の初期化・再代入・resolve recovery も同じ preparation を通します。resolve は保存成功後に回復を確定し、違反時は既存 failure history を保持します。UninitializedBinding は従来どおり read 時の EvalError、真の NoValue は ordinary value validation の対象外です。

期待型は型検査が記録した expression / assignment target、resolved parameter / return、宣言の TypeRef を正本にします。generic は retained call frame の bindings で具体化し、Python 値・implementation ID・`Cell.typ` metadata から型を推測しません。NaN / ±inf などは print の出力、comparison / arithmetic の実行、custom consumer implementation の呼び出しより前に停止します。consumer 違反も FailureEvent を作らず resolve 不可の fatal `ValueContractViolation` です。既存の immutable snapshot と reason を使い、boundary は `value_read` / `call_argument` / `binding_write` / `function_return` / `force_argument` で区別します。

force 開始時にも concrete public Thunk TypeRef により外形と saved failure contract の subset を再検証し、違反時は保存式を実行しません。従来の force 終了時の failure / success validation も維持します。capture 環境は deep validation しませんが、保存式内の checked identifier read にも検証が働きます。low-level host fixture が captured environment の entry を不正な payload / 異なる `Cell.typ` を持つ新しい Cell に置換しても、後続の checked read は静的型に対する違反を検出します。nested Thunk は従来どおり force ごとに一層ずつ評価します。

保証対象は通常 API (`run()` / `execute()` / `eval_program()`) と正式 custom builtin extension を使用する checked evaluation です。Box / Pair / MissingType / `Float[Int]` などの open-world payload は再帰的に検証しません。canonical standard return / parameter として使用する境界では共通 validator が働きます。

直接 `call_builtin()` / `widen_value()` / `eval_expr()` / `_eval_program_with_context(...)`、外部 environment 注入、Cell / Value / ThunkValue constructor、unchecked AST は caller responsibility の low-level API です。ExecutionResult 返却後も payload 内部の mutation は保証対象外です。Phase 3A は custom Python implementation 自体の I/O や mutation を sandbox / rollback しません。Phase 3B は Cell / ThunkValue の field binding を frozen にしますが、captured env の deep copy / isolation、AST mutation 防止、opaque alias 制御、ExecutionResult deep copy は Phase 3D 以降の課題です。検証済み AST の改変や任意に構築した保存式の整合性は Phase 3A で保証しません。

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

この例の評価結果は `9`、`4.5`、`-9` です。実行前に、Int 算術の IntegerOverflow 警告が2件と、未処理の `div` 呼び出しに対する DivideByZero / FloatOverflow 警告が1件表示されます。

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

Type identity は **name + recursive type arguments** です。`Box[Int] ≠ Box[Float]`、`Pair[Int, Float] ≠ Pair[Int, Int]` として区別します。capability / membership identity には latent failures / latent conditions を含めません。`Thunk[Int]<Never>` と `Thunk[Int]<IntegerOverflow>` の capability identity はどちらも `Thunk[Int]` ですが、static compatibility では failure contract の差を保持します。

TypeRef は open-world です。canonical type registry による型名の存在検査はなく、`MissingType` や `Box[MissingType]` も unknown type としては拒否しません。これは型のコンストラクタや値の生成機能を提供するという意味ではありません。

`impl` target・`register` target・`typegroup` member は **concrete identity position** です。ここで concrete identity は、symbolic type variable または contextual `Self` を再帰的な型引数にも含まない TypeRef を指します。

| identity の例 | 上記3か所での使用 |
|---|---|
| `Box[Int]`、`Box[Float]`、`Box[MissingType]`、`Pair[Int, Float]` | 合法 |
| `T`、`Box[T]`、`Box[Box[T]]`、`Pair[Int, T]`、`Self`、`Box[Self]` | 違法 |

`impl Box[Int] guarantees G` の登録は `Box[Int]` にのみ適用し、`Box[Float]` や引数なしの `Box` には適用しません。register と typegroup も同じく完全な identity を区別します。`impl Box[T] guarantees G`、`register Box[T] guarantees G`、`typegroup Group = Box[T]` は未対応で、宣言時に拒否します。generic / wildcard / constructor-wide impl、generic register、generic typegroup の意味付けはありません。

### Cell / ThunkValue の shallow immutability（Phase 3B）

`Cell` は frozen dataclass の immutable binding record です。`value` / `typ` / `mutable` の field reassignment は拒否します。`Cell.mutable` は binding 名を再束縛できるかを表す metadata で、Cell 自体の変更許可ではありません。language-level reassignment は従来どおり RHS の source validation → widening → target validation 後、新しい Cell で environment entry を置換します。初期化・parameter binding・resolve recovery も新しい Cell を作ります。失敗した初期化は `Cell(UninitializedBinding(...))` ではなく独立した `UninitializedBinding` entry で表現します。`Cell.typ` は inspection / ExecutionResult / tests / diagnostics 用に残し、型判断の正本は checked metadata のままです。

`ThunkValue` も frozen dataclass で、`expr` / `env` / `outer` / `potential_failure_contract` の field binding は変更できません。保存 failure contract は constructor で `FailureId` の `set` / `frozenset` のみを受け取り、canonical `FailureSet`（`frozenset`）へ正規化します。元の mutable set を変更しても保存契約は変わらず、その他の iterable や不正 element は TypeError です。result TypeRef metadata は追加していません。

capture は従来の `env.copy()` による shallow mapping snapshot と immutable Cell の共有です。通常の再代入後も Thunk は capture 時の binding を保持します。FunctionEnv の copy も mapping だけを新しくし、Cell・type_bindings を共有して call_frame_id を保持します。generic retained frame / nested Thunk の型置換と nonmemoized evaluation は変わりません。

これは runtime 全体の deep immutability ではありません。captured environment の mapping contents、FunctionEnv metadata、outer が参照する環境、opaque list / dict / custom object の内部、raw checked AST と child list は引き続き mutable です。`t.env["x"].value = ...` は拒否しますが、`t.env["x"] = Cell(...)` は可能です。標準 scalar の正常 payload は実質 immutable ですが、open-world payload の `.append(...)` 等は妨げません。

Phase 3C では custom builtin に opaque ThunkHandle を渡し、identity return は adapter が元の internal ThunkValue に戻します。host constructor は low-level/internal 用に残ります。frozen 化は constructor input 全体の安全性を保証しません。builtin return / custom call argument / force entry の saved ⊆ public 検査、および identifier / widening / typed write / user return / force success の consumer-side validation は引き続き必要です。tests の mapping replacement や expression result replacement は test-only fault injection であり language semantics ではありません。

ExecutionResult の環境は従来の mapping proxy に加え binding record の fields も immutable ですが、payload は deep frozen / deep copied ではありません。Phase 3D 以降の候補は captured env の read-only 化、FunctionEnv immutable 化、read-only ThunkView、validated Thunk factory、LanguageThunk / ExtensionThunk 分離、raw AST exposure の除去と AST collection immutable 化、outer isolation、opaque payload ownership、persistent environment、deep ExecutionResult snapshot、host force capability です。

### Opaque ThunkHandle / extension adapter（Phase 3C）

正式な custom builtin の Python representation は次のとおりです。callable は従来どおり payload だけを受け取り、RuntimeContext や registry は受け取りません。

| Ginger type | Python implementation の値 |
|---|---|
| Int / Int64 | exact `int`（各範囲内） |
| Float | finite `float` |
| Bool | exact `bool` |
| Ordering | canonical tuple `("Ordering", "Left" / "Flat" / "Right")` |
| Unit | `None` |
| open-world type | raw opaque Python payload |
| Thunk[T]<F> | opaque `ginger.extension.ThunkHandle` |

```python
from ginger.extension import ThunkHandle

def identity(t: ThunkHandle) -> ThunkHandle:
    return t
```

ThunkHandle は field を持たない identity-based Python object で、repr は `<ThunkHandle>` です。`expr` / `env` / `outer` / `potential_failure_contract`、Cell、FunctionEnv、retained frame metadata は extension API ではありません。TypeRef や failure contract も公開しません。ThunkValue は runtime internal representation であり、正式 custom builtin の直接の Thunk 引数として渡しません。

全引数の evaluation、source validation → widening → target validation が完了した後、direct Thunk parameter だけを handle に変換します。RuntimeContext の private registry は handle → immutable entry（元の ThunkValue、発行時の concrete public Thunk TypeRef）を保持します。parameter / return 型は checked `resolved_calls` の型を generic frame bindings で具体化したものです。registry は context ごとに空で開始し、entry は追加のみです。ExecutionResult、call frame、failure history、diagnostics へコピーしません。

正常な direct Thunk return は handle の型、current registry membership、issued → expected 型の既存 `typecheck.compatible` 規則を検査して元の ThunkValue に戻し、既存の saved ⊆ public failure contract / runtime value validation を実施します。Int result の handle を Float result に再型付けできません。latent failure の互換性も静的規則と同一です。raw ThunkValue / int / None 等は `wrong_python_type`、手動構築・別 execution の handle は `invalid_extension_handle`、型の不一致は `contract_mismatch` として、`extension_return` 境界の fatal ValueContractViolation になります。FailureEvent は生成せず、実行済み Python side effect は rollback しません。RaisedFailure の経路では return adapter を実行しません。

handle は同一 execution 内で受け取り、Python 側に保存し、別 callable に渡し、後の builtin call から返せます。identity return は capture / frame / saved contract を含め元の internal ThunkValue をそのまま復元します。別 `run()` / RuntimeContext への持ち越しは拒否します。独立して発行された handle が同じ underlying Thunk を表すかは API 契約にせず、Ginger に Thunk equality / identity operation を追加しません。

adapter は direct Thunk 型だけに適用します。nested Thunk は outer handle 1個になり、Ginger `force(...)` が一層ずつ評価します。Box / Pair / MissingType などの open-world payload は再帰変換せず、alias / mutable payload も維持します。内部に raw ThunkValue や ThunkHandle があってもそのままです。opaque payload から取り出した handle を正式 Thunk return として返す際には current registry membership を検査します。

新しい language Thunk の正式 factory、Python host force、result type / public failure contract inspection、serialization、cross-execution transfer は提供しません。作成・評価には Ginger source の `thunk(expr)` / `force(...)` intrinsic を使います。`ThunkValue(...)` constructor と直接 `call_builtin(...)` は low-level/internal responsibility として raw semantics を維持します。これは Python reflection に対する sandbox ではありません。captured env の read-only 化、AST isolation、persistent environment、LanguageThunk / ExtensionThunk 分離も今回の対象外です。

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

この例は `2.0` を出力します。戻り値型は sig に書き、func には書きません。現在採用している sig / func の対応規則は次のとおりです。

- 同名の `sig` が `func` より先に存在する必要があります。
- 引数数が等しく、各位置の型が型名と再帰的な型引数まで一致する必要があります。
- `Thunk[Int]<Never>` と `Thunk[Float]<Never>` は異なる型として照合します。
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

generic position では symbolic TypeRef を使用できます。例えば次の宣言では、引数の `Box[Int]` から T = Int を推論し、戻り値へ置換します。

```ginger
sig extract(Box[T]) -> T {
}
```

T を inference する場所では `Box[T]` を使えますが、concrete identity を登録する場所では使えません。

TypeRef は再帰的に照合・置換するため、`Thunk[T]<Never>` と `Thunk[Int]<Never>` から T = Int を得られます。`SomeType[T]` のような既存 TypeRef 構造も同様ですが、型コンストラクタの不一致は拒否します。置換後の引数全体を既存 compatibility で検査するため、Thunk の結果型を含む型引数の variance は追加しません。generic 本体で推論した Thunk の条件付き潜在 effect は TypeRef に保持し、具体的な binding で既存の FailureSet に置換します。failure 型変数や集合の推論・統合は導入しません。同じ裸の T に異なる latent failure を持つ Thunk 型が渡された場合も、集合の共通上限を新たに推論しません。

型変数決定後に guarantee / typegroup の要件を検査します。`add(1,2i64)` は T = Int64 まで解決した後、Int64 が Addable を持たないため拒否します。Int64 算術は未解禁です。

型検査は呼び出しごとに `Symbols.resolved_calls` へ型変数 binding と置換済み parameter / return TypeRef を保存します。具体型の call は選択実装を持ち、generic 本体の保証メソッド call は必要 guarantee / method を持つ deferred call として保持し、implementation は未確定です。runtime は call-site で解決した `CallFrame.type_bindings` から本体の symbolic TypeRef を置換し、`Symbols.impls[(具体型, guarantee, method)]` から実装を選びます。actual Ginger TypeRef と解決済み parameter TypeRef に従って call boundary widening を行い、Python 値から Int / Int64 や T を推測しません。generic 本体を invocation ごとに再型検査することもありません。

call expression 自体の型は置換済み return TypeRef です。外側の expected type はその後の compatibility 検査にのみ使います。

| 呼び出しと格納先 | call の型・実装・effect |
|---|---|
| `var x: Float = add(1,2)` | call は Int / core.int.add / IntegerOverflow。格納境界で Float に変換 |
| `var x: Float = add(1,2.0)` | call は Float / core.float.add / FloatOverflow。第一引数を Float に変換 |
| `var x: Int = add(1,2.0)` | call は Float。Float → Int は拒否 |
| `var x: Int64 = identity(1)` | identity call は Int。格納境界で Int64 に変換 |

`print(add(1,2))` は `3`（未処理 IntegerOverflow 警告あり）、`print(add(1,2.0))` は `3.0`（未処理 FloatOverflow 警告あり）を出力します。Float 演算の引数式自体が failure を持つ場合は、その effect は従来どおり合算します。

user function の `identity(T) -> T`、`first(T,T) -> T` も同じ推論を使います。`first(1,2.0)` は両 parameter を Float として束縛します。nested call は内側を解決した静的型を外側の evidence に使います。generic 関数フレームは call site で解決した binding を保持し、本体の symbolic TypeRef を置換します。本体内では sig に明示した guarantee を能力証明として使います。Thunk の環境スナップショットは作成元の retained frame ID を保持し、関数終了後の force でも元 frame の型 binding を参照できます。sig / func の厳密な構造照合、型変数名の一致条件は変更しません。

関数本体では型注釈付きの `let name: Type = expr` / `var name: Type = expr` と再代入を使用できます。`let` と parameter は再代入不可、`var` は再代入可能です。宣言型は固定し、トップレベルと同じ安全な `Int → Float` / `Int → Int64` widening を初期化・再代入の境界で適用します。逆方向や `Int64 → Float` は許可しません。型注釈省略の `let x = 1` / `var x = 1` は未対応です。

関数本体全体がひとつのローカルスコープです。引数とローカル変数は同じ名前空間に属し、引数名や宣言済みローカル名の再宣言は禁止します。宣言は順番に処理し、型検査では initializer の検査後に binding を追加します。実行時は initializer が正常値を生成すれば Cell、宣言済み failure により NoValue になれば UninitializedBinding を追加します。宣言前参照や `var x: Int = x` は拒否します。呼び出しごとにローカル環境を作り、関数外や次の呼び出しへ binding は漏れません。

同名のグローバル変数をローカル変数で shadow できますが、グローバル binding 自体は変更しません。既存の名前解決に従い、関数からのグローバル変数の読み取り・代入は型検査で拒否します。そのため同名グローバルがあっても `var x: Int = x` は拒否します。nested block scope・関数内 resolve・nested function・一般的な closure は追加していません。

ローカル宣言では initializer の call を実引数から推論した後、宣言型との compatibility を検査します。次の例は overflow による初期化失敗後、`return y` で未初期化値を要求するため EvalError で停止し、トップレベルの resolve には到達しません。initializer / assignment の failure は既存の関数 failure 上限検査へ合算します。initializer が宣言済み failure で失敗した変数は値を持たない未初期化 binding とし、再代入が失敗した場合は以前の値を維持します。

```ginger
sig increment(Int) -> Int {
    failure IntegerOverflow
}
func increment(x: Int) {
    var y: Int = add(x, 1)
    return y
}
var result: Int = increment(9007199254740991)
resolve result {
    IntegerOverflow {
        result = 999
    }
}
print(result)
```

`failure Never` に置き換えると IntegerOverflow の上限違反です。`return add(x,1)` に宣言戻り値型を渡す推論は行いません。型変数は sig と実引数からの call-site 推論で解決します。ローカルにも `Thunk[Int]<Never>` などを保存でき、latent failure・force・作成時の環境スナップショットは既存の Thunk 規則を維持します。

## guarantee と標準 catalog

`guarantee` method は capability method の型契約の canonical source、`sig` は public generic call surface と public failure/effect contract を定義します。guarantee 経由で dispatch する sig は、method の `Self` を require が指定する型変数へ再帰的に projection し、引数数・引数型・戻り値型を照合します。Thunk の潜在契約も正規化後に一致する必要があり、widening では代用しません。この照合は、func / 直接 builtin を持たず、guarantee requirement が1個で同名 method に対応する sig が対象です。

`impl` は concrete type identity・保証・メソッドから builtin への対応を登録します。保証の存在、必要メソッドの存在、builtin 名の存在などを検査しますが、処理の意味や Python builtin の実際の型契約までは検証していません。

requirement は型変数を宣言・推論するものではなく、すでに sig の parameter から inferable な型変数に制約を加えます。G が宣言済みなら `sig f(T) -> T { require T guarantees G }` は有効です。一方、`sig make() -> T { require T guarantees G }` のような return-only target は宣言時に拒否します。`require T in Group` も同じ制約を持ち、expected return type や登録済み impl の数から T を推論しません。

`require T guarantees G` は、call-site で具体型の保証登録を検査するとともに、generic func 本体で T に使用できる能力を明示します。本体の検査は `tv_guars` の `型変数 → guarantee 集合` を能力証明として参照します。T の具体型が未知でも G のメソッドを使用できますが、require のない操作は拒否します。本体から require を自動推論しません。`require T in Group` は typegroup への所属制約で、guarantee の能力制約とは別です。両方を同じ T に指定できます。

```ginger
sig addTwo(T, T) -> T {
    require T guarantees Addable
    failure IntegerOverflow when T guarantees BoundedArithmetic
    failure FloatOverflow when T guarantees FiniteArithmetic
}
func addTwo(a: T, b: T) {
    return (a + b)
}
print(addTwo(1, 2))
print(addTwo(1.0, 2.0))
```

この例は `3` と `3.0` を出力し、Int の call には IntegerOverflow、Float の call には FloatOverflow の未処理警告が出ます。`return add(a,b)` に置き換えても同じです。`(a + b)` は lower で同じ `add` call に変換されるため、型推論・能力検査・symbolic effect・failure の具体化・runtime dispatch・resolve 資格が一致します。既存の `-` / `*` と sub / mul、`(-a)` と neg も同じ方針です。require を削除すると explicit / infix の両方を定義時に拒否し、`addTwo(1i64,2i64)` は call-site で Addable 不足を拒否します。

能力証明は Addable 専用ではありません。例えば `sig negate(T) -> T { require T guarantees Negatable }` と `func negate(x: T) { return (-x) }` も有効です。メソッドを持つ guarantee には `impl` が必要で、`register` はメソッドを持たない保証に使います。guarantee 間の継承・包含は導入していません。

`ginger/catalog/` の `math`・`cast`・`ordering`・`io`・`lazy` は `core/prelude.py` から自動で読み込みます。JSON の宣言は AST に変換され、ソースの宣言とともにシンボルを構築します。

catalog の `failures` は従来の文字列と条件付き object を混在できます。object は以下の形式だけを許可します。Float の marker 登録は `"registers": [{"type": {"ref": "Float"}, "guarantee": "FiniteArithmetic"}]`、Int は `"registers": [{"type": {"ref": "Int"}, "guarantee": "BoundedArithmetic"}]` と記述します。

```json
{
  "failures": [
    "DivideByZero",
    {
      "failure": "IntegerOverflow",
      "when": {"typevar": "T", "guarantees": "BoundedArithmetic"}
    },
    {
      "failure": "FloatOverflow",
      "when": {"typevar": "T", "guarantees": "FiniteArithmetic"}
    }
  ]
}
```

実行時は `thunk` / `force` を特別扱いし、通常の呼び出しは同名の `func`、sig 直結の builtin、guarantee 経由の impl の順に処理します。最後の経路は保証1個に対応する解決済み型変数から選んだ実装を使います。先頭引数の元の型や Python 実行時型からは選びません。

現在は単一ソースに宣言と実行文を記述できます。`Catalog.ginger`・`Code.ginger`・`Impl.ginger` を役割別に自動読み込みする仕組みはありません。Catalog とソースの責務や標準実装の置換は未確定です。

## failure と binding の解決

現在採用している仕様では、sig に宣言する failure は、呼び出し側が考慮すべき意味のある失敗可能性を記録する契約であり、その関数から外部へ漏れ得る failure の上限です。具体的な契約では、静的に推論した本体の `inferred_failures` が宣言の `declared_failures` の部分集合でなければなりません。generic 本体の条件付き契約には、下記の限定的な包含規則を適用します。

```text
inferred_failures ⊆ declared_failures
```

宣言に、現行実装では実際に発生しない failure が含まれていても許可します。内部で確実に処理され、外へ漏れない failure は公開契約に含めません。実装バグや型検査で防ぐべき問題まで failure として列挙する必要はありません。

sig 本体に `failure DivideByZero` などを列挙できます。個別 FailureId は `core/failure_spec.py` に定義されたものに限られ、宣言済み failureset 名も使用できます。

- failure の省略と `failure Never` は、空集合、つまり「外部へ公開する failure なし」を表します。
- Never と通常 failure の混在は、記述順によらず拒否します。
- 同一 failure の重複（Never の重複を含む）は拒否します。
- failure 名に型引数は付けられません。
- JSON catalog とソースで、Never と重複の扱いを揃えています。

条件付き宣言の構文は `failure <FailureId> when <TypeVariable> guarantees <Guarantee>` だけです。条件の型変数はその sig の有効な型変数、guarantee は宣言済みである必要があります。条件は require とは独立した optional property なので、`when T guarantees BoundedArithmetic` のために `require T guarantees BoundedArithmetic` を追加する必要はありません。

内部の `FailureContract` は無条件の FailureSet と、`ConditionalFailure(FailureId, 型変数, guarantee)` の集合を保持します。generic 本体では条件を保存し、nested generic call は通常の型 binding で条件内の変数も置換します。

```ginger
sig relay(U, U) -> U {
    require U guarantees Addable
    failure IntegerOverflow when U guarantees BoundedArithmetic
    failure FloatOverflow when U guarantees FiniteArithmetic
}
func relay(a: U, b: U) {
    return addTwo(a, b)
}
```

先の addTwo と組み合わせると、callee の `T guarantees BoundedArithmetic` は caller の `U guarantees BoundedArithmetic` に置換されます。FiniteArithmetic の条件も同じく T から U に置換します。具体型が決まった call-site では marker の登録を評価し、`relay(1,2)` は `{IntegerOverflow}`、`relay(1.0,2.0)` は `{FloatOverflow}` に具体化します。この集合を effect・未処理警告・resolve 資格・user function の runtime 契約・`CallFrame.declared_failure_contract` に使います。CallFrame に symbolic contract は保持しません。

上限検査では、FailureId・型変数・guarantee の3要素が完全一致する条件だけを同一視します。無条件の `failure IntegerOverflow` は同じ failure の conditional effect を cover できます。逆に、無条件で推論された IntegerOverflow を conditional declaration だけで cover することはできません。`failure Never` は conditional effect も cover しません。余分な無条件 failure を宣言する既存コードは有効で、Float の call にもその公開契約を残します。

```ginger
var integer: Int = addTwo(9007199254740991, 1)
resolve integer {
    IntegerOverflow {
        integer = 0
    }
}
var floating: Float = addTwo(1.0, 2.0)
```

integer の resolve は許可され、実際の overflow を回復できます。floating の initializer は IntegerOverflow を持たないため、floating に対する同じ handler は静的エラーです。floating は FloatOverflow を持つので、その handler は許可されます。`thunk(addTwo(...))` の潜在契約も concrete binding で既存の FailureSet に具体化し、runtime ThunkValue に symbolic contract は追加しません。

conditional Never、conditional failureset、同一 conditional clause の重複、同じ failure の unconditional / conditional の併記は拒否します。型名比較・typegroup 条件・AND / OR・任意の boolean・runtime 値条件・failure 型変数は未対応です。guarantee inheritance、associated types、overload、advanced typeclass resolution も導入していません。

通常ユーザー関数では、到達可能なローカル宣言の initializer、再代入の右辺、return 式、式文の failure を合算し、未宣言 failure の伝播を型検査で拒否します。通常の呼び出しは callee の sig と引数式の failure を合算します。標準 add / sub / mul は上記の conditional contract を置換して使用します。条件付き宣言に表現されていない実装由来 failure も、該当 guarantee / method の実装から保守的に合算します。最初の無条件 return より後は failure 集計から除外しますが、既存の型検査は続けます。関数内 resolve は未対応です。handled による上限検証の保留は廃止しました。

標準 `div` は `DivideByZero` と `FloatOverflow` を無条件に宣言しています。`div(1.0,2.0)` も両方を持ちます。トップレベルで処理対象にしなければ未処理警告が出ます。値に応じた failure の絞り込みは行わないため、除数がゼロでない呼び出しも警告対象です。

```ginger
var x: Float = div(1.0, 0.0)
resolve x {
    DivideByZero {
        x = 0
    }
    FloatOverflow {
        x = 0
    }
}
print(x)
```

この例は警告なしで `0.0` を出力します。initializer が値を生成できないと、x は型・可変性・原因 event ID を持つ `UninitializedBinding` になります。`resolve x` はその原因 `FailureEvent` の FailureId に一致する handler を実行し、x が有効な値を持った場合だけ元 event を resolved にします。

resolve の静的資格は対象 binding の元の initializer から宣言・推論した failure 集合で検査します。後の代入でこの集合を書き換えません。同一 resolve 内の重複 FailureId、集合外の既知 FailureId、未知名、Never、failureset 名は拒否します。対象は resolve より前に宣言された var に限り、let、未宣言 binding、failure を持たない initializer の var は拒否します。

- resolve はトップレベルの statement です。handler body は既存の statement block を使い、宣言・var 再代入・Unit の式文を書けます。return、関数内 resolve、handler 内 resolve は許可しません。新しい block scope は作らず、実行された handler の宣言はトップレベル環境に追加されます。handler が選択されなければ宣言も実行されず、その binding は作られません。handler 宣言の実行有無を追跡する静的な初期化解析は導入していません。
- 対象が通常値を持っていれば no-op です。他の unresolved event を検索しません。原因 FailureId に一致する handler がない場合も何もしません。
- handler が `print(1)` などを正常に終えても、対象が未初期化のままなら元 event は unresolved のままです。
- handler の代入が新しい宣言済み failure で失敗して対象が未初期化のままなら、元 event と新 event は両方 unresolved です。新 event の `caused_by` は元 event ID になります。元 event を handler の実行だけで resolved にはしません。
- 成功時も event を削除せず、`ExecutionResult.failure_history` に resolved として保持します。`unresolved_events` は status に基づいて除外します。
- FailureContractViolation、EvalError、Python 内部例外は resolve の対象外で、従来どおり fatal です。

トップレベル未処理警告は後続 resolve を含めて集計します。概念的には `(initializer failures - resolve の FailureId) ∪ handler failures` です。initializer が `{IntegerOverflow, DivideByZero}` を持ち、後続 resolve に IntegerOverflow だけを書けば、元 initializer の警告には DivideByZero だけが残ります。handler 内の式や代入の failure は別に合算し、元 initializer の処理対象を指定しただけでは消しません。静的な処理対象の指定は runtime の回復成功を保証しません。警告だけでは実行を止めず、関数本体の上限契約違反を型エラーにする規則は維持します。

builtin の宣言は静的解析が信頼する契約です。Python実装から契約を自動推論しませんが、runtimeでは実際のRaisedFailureを宣言と照合します。独自 builtin sig の宣言漏れは静的には検出できませんが、実際に未宣言 RaisedFailure が出た境界で契約違反として検出します。print / IO の failure 契約は未確定です。有効な Int の toFloat は failure を持ちません。現在、標準 print の出力失敗や、低水準 Python API から不正な巨大整数を直接渡した場合の toFloat 変換失敗は Python 例外として伝播し、通常の `FailureEvent` には変換されません。

旧 `try` / `catch` 構文は削除しました。予約語として保持し、使用すると `try/catch has been removed; use resolve <binding>` という構文エラーになります。失敗し得る式の結果を var の initializer に置き、resolve でその binding を回復してください。

## @attr.handled の廃止（failure runtime 再設計 Phase 1）

`@attr.handled` は廃止しました。sig・func・builtin sig・catalog JSON の属性として指定すると、未知の属性として明示的にエラーになります。旧コードは属性を削除し、通常の failure 宣言と必要に応じた binding の resolve へ移行してください。

failure の種類を指定しない runtime の握りつぶし、呼び出し先の静的 failure の除去、自身・直接依存・間接依存を理由とする上限契約検証の保留は行いません。`FAILURE_CONTRACT_DEFERRED` の note も生成しません。builtin の宣言を信頼する既存の検証境界は引き続き存在します。

汎用の `@attr.<name>` 構文は維持し、`@attr.io` は引き続き使用できます。属性は sig / func の前にのみ置けます。不正な位置の属性は黙って破棄せず、構文エラーにします。

Phase 1 時点では failure 履歴や statement 単位の継続は未導入で、未処理の `RaisedFailure` は例外として伝播して実行を停止していました。属性の廃止と、以下の runtime 導入は別の段階で行いました。`@suppress` は導入していません。

## 内部 runtime の骨格（Phase 2）

`ginger/runtime/failures.py` に `FailureEvent` / `FailureStatus`、`context.py` に `RuntimeContext` / `CallFrame`、`results.py` に `Value` / `NoValue` / `EvalResult` / `ExecutionResult` を追加しました。具体的な静的契約と runtime 境界契約は FailureId の集合、generic 本体の静的契約は無条件集合と conditional clause、実行時履歴は発生ごとに異なる event ID を持つ記録として分離します。

RuntimeContext 内で event / call ID を採番します。イベントとフレームは不変のスナップショットで、context の操作が同じ ID の記録を更新します。更新後の状態は context から再取得します。resolved にしても履歴から削除しません。pending はイベント ID の参照です。Phase 2 時点では関数境界の自動伝播は未実装でしたが、現在は終了時に unresolved な参照だけを親 frame へ伝播します。

`Value(None)` は正常な Unit、`NoValue()` は値の欠落です。ExecutionResult は環境の対応表と履歴のスナップショットを保持しますが、環境内の値そのものを深くコピーしません。未解決イベントは保持した履歴から取得します。

Phase 2 時点では単体で使用できる内部構造のみを導入しました。Phase 3 の接続範囲は以下のとおりです。

## builtin failure bridge（Phase 3）

`ginger/runtime/builtin_bridge.py` を実際の builtin 呼び出し境界へ接続しました。正常終了は `EvalResult(Value(v))`、builtin 自身の宣言済み `RaisedFailure` はイベント登録と `EvalResult(NoValue(), related_event_ids)` に変換します。runtime は具体化済みの sig 契約と標準整数演算の実装別契約を `builtin_failure_contract` で検証します。静的解析では caller-visible な conditional contract を使用し、そこに表現されていない実装由来 failure の保守的な合算に同じ helper を使用します。独自の直接 builtin sig は自身の宣言を使用し、実装名だけで契約を補いません。

引数評価は bridge の外で先に行い、引数の failure を外側 builtin の契約で判定・再登録しません。origin は `core.int.add` などの実装 ID です。実行ごとに内部 RuntimeContext と `<program>` の root call を作り、ユーザー関数内の builtin にも暫定的に同じ root call ID を使用します。これは Phase 3 時点の制限で、Phase 5 で関数別 CallFrame を導入しました。

Phase 3 時点では互換境界 `legacy_value` が NoValue を RaisedFailure へ戻し、既存停止挙動を維持していました。この互換境界は Phase 4 で撤去しました。

Phase 3 時点の未宣言 RaisedFailure の再送出は、Phase 7 で FailureContractViolation に置き換えました。通常イベントとしては登録しません。Python の実装エラーはイベント化しません。標準Float除算自身がゼロ除算をRaisedFailureとして通知します。bridgeはPython内部例外を変換しません。内部テストでは `_eval_program_with_context` に context を渡して通常と同じ経路を観測できます。Phase 8 で pipeline から ExecutionResult と履歴を公開しました。

## statement 単位の継続（Phase 4）

宣言済み builtin failure は `FailureEvent + NoValue` として式の親へ返します。引数は左から右に評価し、NoValue に達したら残りの引数と呼び出し先を実行しません。値を必要とする親演算も実行せず、原因 event ID を保持したまま文を未完遂にし、次の文へ進みます。実行済みの副作用は取り消しません。

```ginger
print(div(1.0, 0.0))
print(3)
```

この例は静的警告に続いて `3` を出力します。最初の print は呼び出されず、DivideByZero のイベントは unresolved のまま残ります。同じ failure の再発は別イベントです。`RuntimeContext.incomplete_statements` で root call ID、scope、文の index / 種類、原因 event ID を内部的に確認できます。index は program items または関数 body 内の0始まりで、source locationではありません。

初期化失敗には `UninitializedBinding` を使用します。型・可変性・原因 event ID のみを保持し、None・0・NoValue を変数の値として保存しません。初期化 failure 自体では停止せず、独立した statement の実行を継続します。ただし後続処理が未初期化 binding の値を要求すると、識別子の読み出し境界で fatal な `EvalError` により runtime を停止します。未宣言変数とは区別した診断に binding 名と原因 event ID を含めます。未初期化状態を NoValue として依存先へ伝播せず、新しい binding も FailureEvent も作成しません。元イベントの履歴・status と未初期化 binding は停止時点まで保持します。この停止は resolve の対象でも FailureContractViolation でもありません。未初期化 var は後の正常代入で回復できます。既存値のある変数の再代入が失敗した場合は既存値を維持します。未宣言変数・型エラー・未宣言 RaisedFailure・Python 内部例外などの致命的エラーでは継続しません。

関数本体の通常文でもNoValueなら次の独立文へ継続します。Phase 5以降は関数ごとのpendingを保持し、正常値・正常Unitと未解決failureが共存できます。明示returnなしで本体末尾へ到達した場合は、途中に未完遂文があっても正常Unitです。return式がNoValueなら関数を終了し、caller側で継続します。

現在の resolve は対象 binding の未初期化原因だけを扱います。handler が実行されたこと自体では resolved にせず、対象 binding が有効な値を得た場合に status を更新します。

Thunk / force は EvalResult の受け渡しに必要な対応のみ行い、遅延評価・snapshot の既存規則は維持します。潜在契約のruntime照合は Phase 9 で追加しました。Phase 4 時点の公開 API は環境辞書でした。Phase 7 で契約違反、Phase 8 で ExecutionResult と履歴公開・main診断を導入しました。正常終了・契約違反時は未初期化bindingも結果環境に保持します。未初期化値の読み出しによる EvalError は他の fatal EvalError と同様に例外として表面化し、ExecutionResult は返しません。内部入口へ渡した環境と RuntimeContext には停止までの状態が残ります。

## 関数呼び出しごとの pending 伝播（Phase 5）

実行開始時の `<program>` に加え、ユーザー関数を呼ぶたびに一意の CallFrame を作成します。parent_call_id は呼び出し元、declared_failure_contract は sig の契約を call-site binding で具体化した FailureSet です。type_bindings は解決済みの Ginger 型を保持します。実際に発生したイベントの pending_event_ids とは別に保持し、関数終了時の runtime 契約検証に使います。

RuntimeContext.current_call_id を scope で切り替え、正常終了・NoValue・fatal 例外のいずれでも親へ復元します。終了したframeの履歴は削除しません。builtinイベントは発生時のcurrent call IDと実装originを保持し、callee終了時は未解決イベントIDだけをcallerへ渡します。イベントは作り直さず、発生元call IDも変更しません。resolvedイベントは履歴に残し、親pendingには追加しません。EvalError や Python 内部例外による fatal 終了時も、それ以前に発生した未解決イベントの参照は親へ伝播します。ただし FailureContractViolation による終了時は pending の親への伝播を停止し、既存のイベント履歴は保持します。

CallFrame.pendingが実行時の正本です。関数のEvalResultには生成された値（またはNoValue）と関連イベントIDを添えます。正常値はpendingがあっても利用でき、値を得たことだけでfailureをresolvedにしません。関数末尾への到達は正常Unit `Value(None)` であり、途中の未完遂文によるpendingがあってもNoValueにはしません。失敗したreturnはPhase 4同様、関数を終了してNoValueを返します。callerはその依存文を未完遂とし、独立した次の文へ進みます。

incomplete statementには実際のユーザー関数call IDが記録されます。resolve の選択根拠は未初期化 binding が保持する直接の原因 event ID であり、関数の pending 全体を処理しません。

同関数の反復呼び出し・再帰も呼び出し単位で区別します。テストの再帰は条件分岐のない現構文に合わせ、host builtinが3回目でfatalエラーを出す有限の実行でframe分離とcleanupを検証しています。FailureContractViolation導入はPhase 7、公開ExecutionResultはPhase 8、returnの終了規則とThunk契約の統合はPhase 9で確定しました。

## binding に基づく resolve runtime

resolve 判定の正本は runtime の binding 状態と FailureEvent 履歴です。

```text
failure 発生
→ initializer が値を生成できない
→ UninitializedBinding が原因 event ID を保持
→ resolve binding
→ 原因 event の FailureId に一致する handler
→ binding が有効な値を得た場合だけ event を resolved に更新
```

`UninitializedBinding.related_event_ids` の既存 tuple は維持します。正常値と pending が共存した earlier event と、最後に値を失わせた直接原因を区別するため、`cause_event_id` property はこの tuple の最新 event ID を返します。引数の NoValue は後続評価を止め、失敗した return は関数を終了するため、最新の関連 event が直接原因になります。resolve はこの ID で `RuntimeContext.get_event` を呼び、同名 event を履歴全体から検索しません。

例えば x と y の initializer がそれぞれ IntegerOverflow を発生させても、`resolve x` は x の原因 event だけを選びます。同じ initializer の途中で別の failure が pending として残っている場合も、直接原因以外の event を resolved にしません。initializer が正常値を生成した binding は、pending があっても通常値として扱い、resolve は no-op です。

handler は既存の block evaluator で statement 単位に継続します。実行後に target binding を再取得し、有効な値があれば元 event の status を更新します。代入が failure で完了しなければ未初期化 binding の原因 tuple は保持します。handler 中の新 event には `caused_by = 元 event ID` を設定し、元 event と区別します。fatal な契約違反・EvalError・Python 例外は通常の終了経路へ変換しません。

resolved event も履歴に残ります。CallFrame.pending_event_ids の参照は伝播履歴として保持し、現在有効な pending は `RuntimeContext.unresolved_pending` が status を照合して取得します。callee 終了時も unresolved だけを caller へ渡します。ExecutionResult の公開フィールドは変更していません。

## Thunk と遅延評価

```ginger
var x: Int = 1
var t: Thunk[Int]<Never> = thunk(x)
x = 2
print(force(t))
print(x)
```

現在の出力は `1`、`2` です。

`thunk(expr)` は型検査後、実行時には式を評価せず保存します。`force(t)` は呼ぶたびに保存した式を再評価し、結果をキャッシュしません。どちらも引数は1個です。force の戻り値は期待型がある文脈では型照合します。

現在は環境辞書を浅くコピーし、外側の環境は参照で保持します。トップレベルの再代入は Cell を置き換えるため、上の例では Thunk が作成時の値を読みます。これはすべての値を深くコピーするという保証ではありません。

正式な型形式は `Thunk[結果型]<failure指定...>` です。`[]` は type arguments、`<>` は failure contract を表します。Thunk の型引数は結果型1個で、failure contract は必須です。`Thunk[Int]` は invalid で、暗黙の `<Never>` はありません。旧形式 `Thunk[Int, Never]` は廃止済みで受理しません。

```text
Thunk[Int]<IntegerOverflow>
      ↑ type argument
           ↑ failure contract
```

合法な形式は `Thunk[Int]<Never>`、`Thunk[Int]<IntegerOverflow>`、`Thunk[Int]<IntegerOverflow, DivideByZero>` です。`Never` は空の failure contract を表す特殊表現なので、`Thunk[Int]<Never, IntegerOverflow>`、`Thunk[Int]<IntegerOverflow, Never>`、空の `Thunk[Int]<>` はいずれも invalid です。failure contract postfix `<...>` は現時点では Thunk のみで、`Box[Int]<IntegerOverflow>` や `Int<Never>` は拒否します。

generic Thunk も合法です。次の宣言では T のみが type inference の対象で、failure contract は type argument ではありません。

```ginger
sig use(Thunk[T]<Never>) -> T {
}
```

```ginger
failureset CalculationFailure { DivideByZero FloatOverflow IOErr }
// 型の例: Thunk[Int]<Never>, Thunk[Int]<DivideByZero>, Thunk[Int]<CalculationFailure>
var t: Thunk[Float]<CalculationFailure> = thunk(div(1.0, 0.0))
var x: Float = force(t)
resolve x {
    DivideByZero {
        x = 0
    }
    FloatOverflow {
        x = 0
    }
    IOErr {
        x = 1
    }
}
print(x)
```

Thunkのfailure指定はforce時に発生しうるfailureの上限契約であり、Thunk作成時のfailureではありません。
`thunk(expr)` は結果型とexprのfailure集合を保存し、作成式のcurrent failureは空です。
`force(t)` は保存された契約をcurrent failureへ復元します。引数を評価するfailureも合算し、
`print(force(t))` にも通常の引数effect伝播で届きます。`force(thunk(expr))` も同じ経路です。
`force(t)` が initializer で失敗すると、対象 var に原因 event ID が保存され、通常の resolve と同じ規則で回復できます。handler 名は公開された潜在契約の個別 FailureId に限ります。潜在契約が failureset で書かれていても、handler にはその集合名を使用できません。正常な force で値を得た binding に対する resolve は no-op です。

failure指定には個別FailureId、failureset、単独のNeverを使えます。`Never` は failure contract が空であることを表し、通常の FailureId ではありません。例: `Thunk[Int]<Never>`、`Thunk[Int]<IntegerOverflow, DivideByZero>`。
指定省略、未知名、Neverとの混在、同じ指定名の重複は拒否します。failureset展開後の重なりは統合します。
代入・関数引数・戻り値では、結果型の完全一致とactualの潜在failure集合がdeclaredの部分集合であることを検査します。
sig/funcの引数宣言は展開後の集合も含めて再帰的に完全一致させます。一般的な部分型や結果型のvarianceは導入していません。

Thunkを返す関数の自身のfailureと、返したThunkの潜在failureは別契約です。
`sig make() -> Thunk[Float]<DivideByZero, FloatOverflow> { failure Never }` は有効です。
静的に解決されたThunkは本体上限検証の対象です。
ThunkValue は既存の推論済み潜在 failure 契約を保持し、force 時に検証します。catalogのthunk/forceのTはintrinsicのプレースホルダーで、
実際の型・failureは専用規則で計算します。通常のcatalog Thunk型では`args`に結果型1個、`failures`に指定名配列を記述します。

## 現在の制限・未確定事項

| 項目 | 制限・判断が必要な点 |
|---|---|
| 型変数推論 | sig 仮引数と実引数から call site ごとに推論。外側 expected type / 関数本体からの逆推論はしない |
| 型宣言・コンテナ | nominal type declarations、type aliases、Array は未実装 |
| 型引数・ジェネリクス | TypeRef の再帰照合・置換に対応。一般的な variance、failure 型変数、overload、alpha-equivalence は未対応 |
| guarantee の契約 | 明示した require を generic 本体の能力証明に使用。型・guarantee・method による既存 impl dispatch を維持し、継承・associated types・overload は未対応 |
| failure の契約 | 型変数の guarantee 条件と完全一致による上限検証に対応。一般的な条件論理や conditional failureset は未対応。builtin 実装の自動推論はなく、print / IO の契約は未確定。未処理は警告 |
| resolve | トップレベルの var のみ。let・関数内・handler 内 resolve、failureset handler、全種 failure を処理する handler、failure 値の取得構文は未対応 |
| handled | 廃止済み。sig / func / builtin sig で指定するとエラー |
| Thunk | 明示的な潜在failure上限契約を保持し、forceで復元する。本体上限契約を検証 |
| Catalog とソース | 役割の強制や複数ファイルの読み込みはない |
| 名前付き引数 | 構文解析は対応するが、通常の型検査・実行では拒否 |
| スコープ | 関数外変数への参照は通常の型検査で拒否。評価器には外側環境を参照する処理が残る |
| 関数内ローカル変数 | 型注釈付き let / var と var 再代入に対応。型注釈省略・nested block scope・関数内 resolve は未対応 |
| CLI | main がサンプル固定。入力パス指定の CLI は未実装 |
| String・制御構文など | 文字列リテラル、条件分岐、ループ、ユーザー定義データ構造は未実装。String の実行時処理や builtin は一部存在する |

未実装項目は、今後追加すべき必須機能を意味しません。対象に含めるかどうかも含め、別途判断が必要です。

## テスト

`tests/test_parameterized_type_identity.py` は再帰的 identity・specialized dispatch・open-world・effect との分離、`tests/test_concrete_identity_positions.py` は登録位置の symbolic / Self 拒否と generic inference の維持を確認します。`tests/test_thunk_syntax.py` は新構文・旧構文拒否・Never、`tests/test_guarantee_method_signature.py` は Self projection と型契約の一致、`tests/test_requirement_typevars.py` は parameter から inferable な requirement target を確認します。

failure 契約の自動回帰テストは `tests/test_failure_contract.py` にあります。上限契約、宣言正規化、handled の拒否・Thunk / builtin の契約と、既存の型検査・サンプルの回帰確認を担います。`tests/test_generic_guarantees.py` は generic 本体の能力証明、explicit / lowered call の一致、deferred dispatch と frame binding を確認します。`tests/test_conditional_failures.py` は Int / Float の具体的 effect、nested substitution、resolve / Thunk、catalog と runtime 契約を確認します。`tests/test_float_finite.py` は finite literal、正負の Float overflow、ゼロ除算優先順位、不正な外部入力、alias、resolve / Thunk を確認します。`tests/test_builtin_value_contract.py` は non-finite return の拒否と他型・opaque 型の回帰を確認します。`tests/test_resolve.py` は静的資格、no-op、原因 event の分離、回復条件、部分 resolve の警告、handler failure と caused_by、Thunk/force、旧構文拒否を確認します。履歴・契約違反・statement 継続の既存テストも resolve に移行しています。

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
| `ginger/typecheck.py` | 型・制約・failure 契約と resolve 資格の静的検査、call-site 推論 |
| `ginger/eval.py` | 文・式の評価、binding 回復の確認、ユーザー関数と Thunk の評価境界 |
| `ginger/builtin.py` | Python の組み込み実装と静的解析・runtime 共通の builtin failure 契約 |
| `ginger/runtime/` | event / call frame 履歴・status・caused_by、契約検証、builtin bridge、評価結果・実行スナップショット、Thunk の保持データ |
| `ginger/catalog/` / `core/` | 標準 JSON 定義、読み込み、failure 定義 |
| `ginger/scripts/` | 現行の動作例と過去の試行サンプル |
| `tests/test_failure_contract.py` | failure 契約・検証境界・既存動作の自動回帰テスト |
| `tests/test_resolve.py` | binding に基づく resolve の静的検査・runtime・警告の回帰テスト |

## 開発上の注意

この README は現時点の実装状況の記録です。未確定事項はコードや README のどちらかを正と決めつけず、設計判断を先に行います。

古い `catalog` / `fn` / `args:` 構文、`run_ginger.py` による起動、Catalog / Code の自動分離は現行の使い方ではありません。`scripts/` の古いサンプルやコメントにも不一致があります。既存ファイルの存在だけを、対応機能や互換性の保証とは扱わないでください。

### failureset（第1段階）

既存の FailureId 集合に名前を付け、sig の failure 宣言で参照できます。

```ginger
failureset CalculationFailure {
    DivideByZero
    FloatOverflow
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
その定義が必要です。failureset は既存の文字列 entry を維持し、conditional object の failure には指定できません。

要素は直接の既知 FailureId に限定します。未知の要素、要素の重複、`Never`、
集合名の重複、ネスト（自己参照を含む）はエラーです。空の定義は現段階では未対応として
拒否します。FailureId および `Never` と同名の集合は曖昧になるため禁止し、
type・guarantee・typegroup・関数とは既存の別々の名前表に合わせて同名を許容します。

集合名自体は FailureId ではなく、resolve の handler 名には使用できません。対象 binding の
initializer から静的に発生しうる個別の FailureId を指定してください。集合演算は未導入です。`@attr.handled` は上記 Phase 1 で廃止しました。

## runtime failure 契約検証（Phase 7）

宣言済みfailureは引き続きイベントとして保持し、statement単位で継続できます。unresolvedであること自体は契約違反ではなく、root programは未処理イベントを保持できます。

builtinは引数評価後、実装自身が送出したRaisedFailureだけを、具体化済みのsig契約と選択された実装のruntime契約を扱う `builtin_failure_contract` で照合します。caller-visible な静的 conditional contract とは区別します。直接builtin aliasやcustom builtinも自身のsig契約で判定し、callerの宣言で救済しません。未宣言failureは通常イベントを作らず、fatalな `FailureContractViolation` として停止します。Python内部例外はこの型へ変換しません。

ユーザー関数は戻り値をcallerへ渡す直前に、CallFrameのunresolved pendingを自身のsig契約と照合します。callee由来イベントも対象ですが、resolved履歴は対象外です。正常値・Unit・NoValueのいずれでも同じ検証を行います。合法なイベントは同じIDのまま伝播します。違反時は既存履歴をunresolvedのまま残し、通常のpending伝播を停止します。current frameは例外経路でも復元します。

Violationはresolveの対象ではありません。handler中の違反はfatalで、元の原因eventをresolvedにはしません。診断にはfailure、origin、違反境界名、call ID、宣言集合、存在する場合event IDを含めます。内部RuntimeContextの `last_contract_violation` で参照できます。公開ExecutionResultとmain表示への統合はPhase 8です。

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

RuntimeContextは実行中の状態、ExecutionResultは終了時点のsnapshotです。履歴と未完遂記録は不変レコードのtuple、frame対応表はコピーした読み取り専用mapping、違反情報は例外から切り離したfrozen dataclassです。環境対応表もコピーして読み取り専用にします。Cell は frozen binding record ですが、binding や Ginger 値の payload は deep copy / deep freeze しません。

unresolvedだけなら正常に完走して結果を返します。契約違反はGingerプログラムを即停止しますが、公開APIは停止時点の結果を返します。ユーザー関数境界の違反では原因eventを履歴に残し、builtin未宣言failureではeventを作りません。違反で伝播が止まったeventはroot pendingに未到達でも `unresolved_events` に含まれます。正常完走時もraw pendingにはresolved参照が残り得るため、現在の未解決状態はevent.statusで判定します。

Python内部例外、parse/typecheckエラーは結果へ隠さず従来の例外として送出します。内部 `_eval_program_with_context` は評価・テスト用の例外経路を維持します。コンパイル診断はruntime結果へ統合しません。

mainはunresolvedイベントを1件ずつstderrへ表示し、failure名・event ID・origin・call IDを含めます。resolved履歴は自動表示しません。契約違反もstderrへ専用診断を出し終了コード1、正常完走はunresolvedの有無によらず0です。既存の静的警告もstderrへ移しました。Python内部例外は隠しません。

Phase 9でreturnの既存終了規則を確定し、Thunk/forceの潜在契約・call帰属を統合しました。

## returnと遅延評価（Phase 9）

`return expr` に到達すると、Value/NoValueにかかわらず現在の関数を終了します。NoValueでも後続文や第二returnは実行せず、静的解析のreturn以降到達不能という規則を維持します。NoValueは原因event IDとともにcallerへ返り、callerの依存statementを未完遂にした後、次の独立statementで継続します。正常値・Unit・NoValue を caller へ返す前には関数の failure 契約を検証します。評価途中の EvalError や Python 内部例外ではこの検証へ進まず、元の例外を送出します。

正常値とpending failureは共存し、正常Unit `Value(None)` とNoValueは別です。未初期化bindingの値を別変数のinitializer・print・returnなどが要求すると EvalError で停止します。原因event IDはbindingに保持しますが、NoValueとして伝播せず、新eventも生成しません。

Thunkは非memoizedです。作成時には式を実行せず、FailureEventも生成しません。作成時のlexical environmentと、型検査で推論した潜在failure契約を保持します。forceごとに再評価するため、実際に同じfailureが再発すれば別eventになります。既存の環境capture方式は変更していません。

force時は通常のEvalResult経路を使います。NoValueなら親演算を実行せず、正常値とpendingが共存するなら両方を保持します。builtinとユーザー関数の契約検証を通した後、今回のforce中に新しく生じたunresolved eventを、Thunkが保持する推論契約およびforce引数の公開Thunk型の潜在契約に照合します。契約外なら境界種別 `thunk`、境界名 `force` のFailureContractViolationとして停止し、既存eventを履歴へ残します。

Phase 2.5 の builtin return 時の保存契約 subset 検査後も、この force 時の二重の failure 契約検証を維持します。Phase 3A では保存 metadata の契約超過を read / argument / force 開始時にも検出し、実 failure が出なくても停止します。その検証後に成功値だけを value contract で検査し、違反は `ExecutionResult.value_contract_violation` に記録します。FailureContractViolation と ValueContractViolation は別の診断です。

Thunkが未初期化bindingをcaptureすること自体は許可します。forceがその値を実際に要求すると同じ読み出し境界で EvalError により停止し、過去の原因eventを再発生させず、新しいFailureEventや潜在failureとして扱いません。lexical environment snapshotは維持するため、capture後に元のvarを回復しても、snapshot内の未初期化bindingをforceで読むと停止します。

force専用CallFrameは追加していません。builtin failureはforce実行中のcurrent callへ帰属し、Thunk内のユーザー関数呼び出しは通常のchild frameを作ります。lexical environmentを作成元から取得してもcall identityを作成元へ戻しません。

force中に生成されたeventがbindingの初期化失敗の原因になれば、resolveでそのbindingを回復できます。handler内forceの新eventには通常どおりcaused_byが付き、対象が回復しなければ元eventもunresolvedのままです。Python内部例外は通常の例外として表面化し、Violationは既存のExecutionResult.contract_violationへ、その他の履歴・frame・未完遂記録も既存フィールドへ公開します。公開結果フィールドは変更していません。
