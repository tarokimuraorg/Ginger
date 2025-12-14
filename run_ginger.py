def skip_spaces(s, i):
    
    while i < len(s) and s[i].isspace():
        i += 1
    
    return i

def parse_name(s, i):
    
    i = skip_spaces(s, i)
    
    start = i
    
    # 関数名は英数と'_'(アンダースコア)のみ許可
    while i < len(s) and (s[i].isalnum() or s[i] == "_"):
        i += 1
    
    if start == i:
        raise SyntaxError(f"name expected at {i}")
    
    return s[start:i], i

def parse_number(s, i):
    
    i = skip_spaces(s, i)
    start = i
    
    while i < len(s) and s[i].isdigit():
        i += 1
    
    if start == i:
        raise SyntaxError(f"number expected at {i}")
    
    return int(s[start:i]), i

def parse_expr(s, i=0):

    i = skip_spaces(s,i)
    
    if i < len(s) and s[i].isdigit():
        value, i = parse_number(s, i)
        return ["num", value], i
    
    name, i = parse_name(s, i)
    i = skip_spaces(s, i)

    # 引数なし呼び出し
    if i >= len(s) or s[i] != "(":
        return ["call", name, []], i
    
    # skip '('
    i += 1
    args = []
    i = skip_spaces(s, i)

    if i < len(s) and s[i] == ")":
        i += 1
        return ["call", name, args], i
    
    node, i = parse_expr(s, i)
    args.append(node)

    while True:
        i = skip_spaces(s, i)
        if i < len(s) and s[i] == ",":
            i += 1
            node, i = parse_expr(s, i)
            args.append(node)
        else:
            break

    i = skip_spaces(s, i)

    if i >= len(s) or s[i] != ")":
        raise SyntaxError(f"')' expected at {i}")
    
    i += 1

    return ["call", name, args], i

def parse_code(src: str):

    node, i = parse_expr(src, 0)
    i = skip_spaces(src, i)

    if i != len(src):
        raise SyntaxError(f"extra text at {i}")
    
    return node

#-----------------
# Catalog loader
#-----------------

def load_catalog(path: str):
    """
    Reads:
        catalog <Name>
        type <TypeName>
        func <Name>
            args: ...
            return: ...
            description: ...

    Returns:
        {
            "catalog_name": "...",
            "types": set([...]),
            "funcs": {
                "add": {"args": ["Int", "Int"], "return": "Int", "description": "..."}
            }
        }
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    catalog_name = None
    types = set()
    funcs = {}

    current_func = None

    for raw in lines:
        
        line = raw.strip()

        # '//'を文の冒頭に置くことでコメントアウトする
        if not line or line.startswith("//"):
            continue

        if line.startswith("catalog "):
            catalog_name = line.split(None, 1)[1].strip()
            continue

        if line.startswith("type "):
            t = line.split(None, 1)[1].strip()
            types.add(t)
            continue

        if line.startswith("func "):
            name = line.split(None, 1)[1].strip()
            current_func = name
            funcs[current_func] = {"args": [], "return": None, "description": None}
            continue

        # func内の属性
        if current_func is not None:

            if line.startswith("args:"):

                rhs = line.split(":",1)[1].strip()
                args = [a.strip() for a in rhs.split(",") if a.strip()]
                funcs[current_func]["args"] = args
                continue

            if line.startswith("return:"):
                rhs = line.split(":", 1)[1].strip()
                funcs[current_func]["return"] = rhs
                continue

            if line.startswith("description:"):
                rhs = line.split(":", 1)[1].strip()
                if rhs.startswith('"') and rhs.endswith('"') and len(rhs) >= 2:
                    rhs = rhs[1:-1]
                funcs[current_func]["description"] = rhs
                continue

        raise SyntaxError(f"Unknown line in catalog: {raw}")
    
    if catalog_name is None:
        raise SyntaxError("catalog <Name> is required")
    
    for funcion_name, spec in funcs.items():
        if spec["return"] is None:
            raise SyntaxError(f"func {funcion_name}: return is required")
        
    return {"catalog_name": catalog_name, "types": types, "funcs": funcs}

# GingerとPythonの型をマッピング
TYPEMAP = {
    "Int": int
}

#-------------
# Evaluator
#-------------

def eval_ast(ast, catalog):
    
    kind = ast[0]

    if kind == "num":
        return ast[1]
    
    if kind == "call":
        func_name = ast[1]
        args_ast = ast[2]
        
        if func_name not in catalog["funcs"]:
            raise RuntimeError(f"Unknown function: {func_name}")
        
        spec = catalog["funcs"][func_name]
        expected_n = len(spec["args"])

        if len(args_ast) != expected_n:
            raise RuntimeError(
                f"{func_name}: expected {expected_n} args, got {len(args_ast)}"
            )
        
        # 引数評価
        values = [eval_ast(a, catalog) for a in args_ast]

        # 引数の型チェック
        for idx, (type_name, v) in enumerate(zip(spec["args"], values)):

            pytype = TYPEMAP.get(type_name)
            
            if pytype is None:
                raise RuntimeError(f"Unknown type in catalog: {type_name} (in func {func_name})")
            
            if not isinstance(v, pytype):
                raise RuntimeError(
                    f"{func_name}: arg({idx}) must be {type_name}, got {type(v).__name__}"
                )

        if func_name == "add":
            
            result = values[0] + values[1]
            ret_pytype = TYPEMAP.get(spec["return"])

            # 戻り値の型チェック
            if ret_pytype is None:
                raise RuntimeError(f"Unknown return type in catalog: {spec['return']} (in func {func_name})")
            
            if not isinstance(result, ret_pytype):
                raise RuntimeError(
                    f"{func_name}: return must be {spec['return']}, got {type(result).__name__}"
                )
            
            return result
        
        raise RuntimeError(f"Function not implemented yet: {func_name}")
    
    raise RuntimeError(f"Unknown AST node: {kind}")

def main():

    catalog = load_catalog("Catalog.ginger")

    with open("Code.ginger", "r", encoding="utf-8") as f:
        code = f.read().strip()

    ast = parse_code(code)

    print("== Catalog ==")
    print(catalog)
    print("\n== Code ==")
    print(code)
    print(ast)
    print("\n== Result ==")
    result = eval_ast(ast, catalog)
    print(result)

if __name__ == "__main__":
    main()