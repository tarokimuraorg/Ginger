# FloatUnderflow 作業報告

Float は有限の正規化 binary64 と符号付きゼロのみを許容する契約に変更しました。算術結果検証と runtime value validation は同じ値域判定を使い、FloatUnderflow は既存の FailureEvent / NoValue / resolve を利用します。FiniteArithmetic marker を再利用し、generic・Thunk・sig の上限検証にも公開しています。

## 検証

- `python3 -m unittest discover -s tests`: **542テスト成功**（既存531、新規11。subTest による境界値・正負・演算別ケースも含む）。
- `git diff --check`: 成功。
- `python3 -m ginger.cli run ginger/scripts/Scene_184.ginger`: 終了コード0。
- Scene_184 の出力: `0.0`, `0.0`, `0.0`, `-0.0`, `0.30000000000000004`, `-0.0`。
- Scene_184 のイベント: FloatUnderflow 2件、両方 resolved として履歴に保持。未解決0件、fatalなし。
- Scene_184 の静的警告: `{FloatOverflow}` 2件、`{FloatOverflow, FloatUnderflow}` 3件。値に基づく契約の絞り込みはしていません。
- リテラル手動確認: `5e-324` / `1e-400` は静的エラー、`0e-400` / `-0.0` は受理、`1e400` は従来の非有限リテラルエラー。

## 正常なゼロの判定

丸め後の `result == 0.0` だけでは判定していません。正常な有限入力に対し add は `a == -b`、sub は `a == b`、mul はどちらかがゼロ、div は分子がゼロを正確なゼロの条件とします。除数が符号付きゼロなら DivideByZero が優先です。正確なゼロを誤って failure にするケースはテストで発生していません。ゼロでない subnormal 結果と正負のゼロへの underflow は NoValue となり、正常値として公開されません。通常の丸めを補正していません。

外部 subnormal は `ValueContractViolation(reason='subnormal')` で拒否し、算術 failure のイベントは生成しません。builtin return、引数、binding write、identifier read、function return、Thunk/force、および print / comparison への流入防止を検証しました。

## 名称調査・未解決事項

詳細と参照資料は [README の名称調査](README.md#数値型の名称に関する調査今回の実装変更なし) に記載しました。

- Int64 は互換性のため維持する提案です。対称範囲は failure のない neg を支えますが、Ord と Int からの widening は対称性を必要としません。通常の signed 64-bit から取り込む際には `-2^63` の拒否が必要です。
- WideInt は簡潔ですが範囲が不明瞭、SymmetricInt64 は契約が明確ですが長い名前です。
- 将来の全 binary64 型は同精度でも値契約が異なるため別型として妥当です。Binary64 を第一候補とする提案です。Float64 は馴染みやすい一方で現在の Float との違いが不明瞭、IEEEFloat64 は意図が明確ですが全面的な IEEE 準拠を連想させます。
- 今回の実装範囲に未解決事項はありません。将来型の NaN 順序、特殊値の演算規則、NaN payload / signaling NaN の保持、明示変換の失敗契約は未決定です。改名・新型追加は行っていません。

## 変更ファイル

作業開始時から変更されていた Scene_23.ginger と Scene_27.ginger には触れていません。ブランチ変更、commit、push、reset は行っていません。

### 実装・catalog（9ファイル）

- [ginger/ast.py](ginger/ast.py)
- [ginger/builtin.py](ginger/builtin.py)
- [ginger/catalog/math.json](ginger/catalog/math.json)
- [ginger/core/failure_spec.py](ginger/core/failure_spec.py)
- [ginger/numeric.py](ginger/numeric.py)
- [ginger/parser.py](ginger/parser.py)
- [ginger/runtime/builtin_bridge.py](ginger/runtime/builtin_bridge.py)
- [ginger/runtime/value_contract.py](ginger/runtime/value_contract.py)
- [ginger/typecheck.py](ginger/typecheck.py)

### テスト（22ファイル）

- [tests/test_builtin_bridge.py](tests/test_builtin_bridge.py)
- [tests/test_builtin_value_contract.py](tests/test_builtin_value_contract.py)
- [tests/test_call_frames.py](tests/test_call_frames.py)
- [tests/test_call_inference.py](tests/test_call_inference.py)
- [tests/test_conditional_failures.py](tests/test_conditional_failures.py)
- [tests/test_consumer_value_contract.py](tests/test_consumer_value_contract.py)
- [tests/test_contract_violation.py](tests/test_contract_violation.py)
- [tests/test_execution_result.py](tests/test_execution_result.py)
- [tests/test_failure_contract.py](tests/test_failure_contract.py)
- [tests/test_failureset.py](tests/test_failureset.py)
- [tests/test_float_finite.py](tests/test_float_finite.py)
- [tests/test_float_underflow.py](tests/test_float_underflow.py)
- [tests/test_generic_guarantees.py](tests/test_generic_guarantees.py)
- [tests/test_integer_overflow.py](tests/test_integer_overflow.py)
- [tests/test_local_resolve.py](tests/test_local_resolve.py)
- [tests/test_resolve.py](tests/test_resolve.py)
- [tests/test_return_force_runtime.py](tests/test_return_force_runtime.py)
- [tests/test_statement_continuation.py](tests/test_statement_continuation.py)
- [tests/test_thunk_contract.py](tests/test_thunk_contract.py)
- [tests/test_thunk_value_contract.py](tests/test_thunk_value_contract.py)
- [tests/test_unary_minus.py](tests/test_unary_minus.py)
- [tests/test_uninitialized_reads.py](tests/test_uninitialized_reads.py)

### Scene（契約・警告の更新と手動確認用184の追加）（47ファイル）

- [ginger/scripts/Scene_1.ginger](ginger/scripts/Scene_1.ginger)
- [ginger/scripts/Scene_104.ginger](ginger/scripts/Scene_104.ginger)
- [ginger/scripts/Scene_105.ginger](ginger/scripts/Scene_105.ginger)
- [ginger/scripts/Scene_106.ginger](ginger/scripts/Scene_106.ginger)
- [ginger/scripts/Scene_12.ginger](ginger/scripts/Scene_12.ginger)
- [ginger/scripts/Scene_13.ginger](ginger/scripts/Scene_13.ginger)
- [ginger/scripts/Scene_14.ginger](ginger/scripts/Scene_14.ginger)
- [ginger/scripts/Scene_147.ginger](ginger/scripts/Scene_147.ginger)
- [ginger/scripts/Scene_148.ginger](ginger/scripts/Scene_148.ginger)
- [ginger/scripts/Scene_15.ginger](ginger/scripts/Scene_15.ginger)
- [ginger/scripts/Scene_158.ginger](ginger/scripts/Scene_158.ginger)
- [ginger/scripts/Scene_159.ginger](ginger/scripts/Scene_159.ginger)
- [ginger/scripts/Scene_16.ginger](ginger/scripts/Scene_16.ginger)
- [ginger/scripts/Scene_160.ginger](ginger/scripts/Scene_160.ginger)
- [ginger/scripts/Scene_162.ginger](ginger/scripts/Scene_162.ginger)
- [ginger/scripts/Scene_163.ginger](ginger/scripts/Scene_163.ginger)
- [ginger/scripts/Scene_164.ginger](ginger/scripts/Scene_164.ginger)
- [ginger/scripts/Scene_165.ginger](ginger/scripts/Scene_165.ginger)
- [ginger/scripts/Scene_166.ginger](ginger/scripts/Scene_166.ginger)
- [ginger/scripts/Scene_17.ginger](ginger/scripts/Scene_17.ginger)
- [ginger/scripts/Scene_173.ginger](ginger/scripts/Scene_173.ginger)
- [ginger/scripts/Scene_184.ginger](ginger/scripts/Scene_184.ginger)
- [ginger/scripts/Scene_19.ginger](ginger/scripts/Scene_19.ginger)
- [ginger/scripts/Scene_24.ginger](ginger/scripts/Scene_24.ginger)
- [ginger/scripts/Scene_25.ginger](ginger/scripts/Scene_25.ginger)
- [ginger/scripts/Scene_3.ginger](ginger/scripts/Scene_3.ginger)
- [ginger/scripts/Scene_30.ginger](ginger/scripts/Scene_30.ginger)
- [ginger/scripts/Scene_36.ginger](ginger/scripts/Scene_36.ginger)
- [ginger/scripts/Scene_37.ginger](ginger/scripts/Scene_37.ginger)
- [ginger/scripts/Scene_38.ginger](ginger/scripts/Scene_38.ginger)
- [ginger/scripts/Scene_39.ginger](ginger/scripts/Scene_39.ginger)
- [ginger/scripts/Scene_40.ginger](ginger/scripts/Scene_40.ginger)
- [ginger/scripts/Scene_41.ginger](ginger/scripts/Scene_41.ginger)
- [ginger/scripts/Scene_42.ginger](ginger/scripts/Scene_42.ginger)
- [ginger/scripts/Scene_43.ginger](ginger/scripts/Scene_43.ginger)
- [ginger/scripts/Scene_45.ginger](ginger/scripts/Scene_45.ginger)
- [ginger/scripts/Scene_46.ginger](ginger/scripts/Scene_46.ginger)
- [ginger/scripts/Scene_47.ginger](ginger/scripts/Scene_47.ginger)
- [ginger/scripts/Scene_48.ginger](ginger/scripts/Scene_48.ginger)
- [ginger/scripts/Scene_49.ginger](ginger/scripts/Scene_49.ginger)
- [ginger/scripts/Scene_5.ginger](ginger/scripts/Scene_5.ginger)
- [ginger/scripts/Scene_50.ginger](ginger/scripts/Scene_50.ginger)
- [ginger/scripts/Scene_6.ginger](ginger/scripts/Scene_6.ginger)
- [ginger/scripts/Scene_68.ginger](ginger/scripts/Scene_68.ginger)
- [ginger/scripts/Scene_69.ginger](ginger/scripts/Scene_69.ginger)
- [ginger/scripts/Scene_8.ginger](ginger/scripts/Scene_8.ginger)
- [ginger/scripts/Scene_9.ginger](ginger/scripts/Scene_9.ginger)

### 文書（2ファイル）

- [FLOAT_UNDERFLOW_REPORT.md](FLOAT_UNDERFLOW_REPORT.md)
- [README.md](README.md)
