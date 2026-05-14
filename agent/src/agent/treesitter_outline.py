# agent/treesitter_outline.py
"""
Tree-sitter based parsing for Java and JavaScript files.
Provides outline extraction, symbol finding, call chain tracing, and syntax checking.
Used by tools.py to support non-Python languages.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# Language extension mapping
TREESITTER_EXTENSIONS = {
    '.java': 'java',
    '.js': 'javascript',
    '.jsx': 'javascript',
    '.mjs': 'javascript',
    '.cjs': 'javascript',
    '.go': 'go',
    '.ts': 'typescript',
    '.tsx': 'typescript',
}


def _get_parser(lang: str):
    """Get a tree-sitter parser for the given language."""
    import tree_sitter
    if lang == 'java':
        import tree_sitter_java
        language = tree_sitter.Language(tree_sitter_java.language())
    elif lang == 'javascript':
        import tree_sitter_javascript
        language = tree_sitter.Language(tree_sitter_javascript.language())
    elif lang == 'go':
        import tree_sitter_go
        language = tree_sitter.Language(tree_sitter_go.language())
    elif lang == 'typescript':
        import tree_sitter_typescript
        language = tree_sitter.Language(tree_sitter_typescript.language_typescript())
    else:
        raise ValueError(f"Unsupported language: {lang}")
    return tree_sitter.Parser(language)


def _node_text(node, source_bytes: bytes) -> str:
    """Extract text for a node."""
    return source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace')


def _find_child_by_type(node, *types):
    """Find first child matching one of the given types."""
    for child in node.children:
        if child.type in types:
            return child
    return None


def _find_children_by_type(node, *types):
    """Find all children matching one of the given types."""
    return [c for c in node.children if c.type in types]


# ---------------------------------------------------------------------------
# Java outline / symbol
# ---------------------------------------------------------------------------

def _java_format_params(node, source_bytes: bytes) -> str:
    """Format Java method/constructor parameters."""
    params_node = _find_child_by_type(node, 'formal_parameters')
    if not params_node:
        return '()'
    params = []
    for child in params_node.children:
        if child.type == 'formal_parameter':
            type_node = _find_child_by_type(child, 'type_identifier', 'generic_type',
                                             'array_type', 'integral_type', 'floating_point_type',
                                             'boolean_type', 'void_type', 'scoped_type_identifier')
            name_node = _find_child_by_type(child, 'identifier')
            type_str = _node_text(type_node, source_bytes) if type_node else '?'
            name_str = _node_text(name_node, source_bytes) if name_node else '?'
            params.append(f"{type_str} {name_str}")
        elif child.type == 'spread_parameter':
            params.append(_node_text(child, source_bytes))
    return f"({', '.join(params)})"


def _java_get_modifiers(node, source_bytes: bytes) -> str:
    """Get modifier keywords (public, static, etc.)."""
    mods_node = _find_child_by_type(node, 'modifiers')
    if not mods_node:
        return ''
    parts = []
    for child in mods_node.children:
        if child.type in ('public', 'private', 'protected', 'static', 'final',
                          'abstract', 'synchronized', 'native', 'default'):
            parts.append(child.type)
        elif child.type == 'marker_annotation' or child.type == 'annotation':
            parts.append(_node_text(child, source_bytes))
    return ' '.join(parts)


def _java_get_return_type(node, source_bytes: bytes) -> str:
    """Get return type for a method."""
    for child in node.children:
        if child.type in ('type_identifier', 'generic_type', 'array_type',
                          'integral_type', 'floating_point_type', 'boolean_type',
                          'void_type', 'scoped_type_identifier'):
            return _node_text(child, source_bytes)
    return ''


def _java_outline(source: str) -> list:
    """Parse Java source and return outline entries."""
    parser = _get_parser('java')
    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)
    root = tree.root_node
    outline = []

    for node in root.children:
        if node.type == 'class_declaration':
            outline.append(_java_class_outline(node, source_bytes))
        elif node.type == 'interface_declaration':
            outline.append(_java_interface_outline(node, source_bytes))
        elif node.type == 'enum_declaration':
            outline.append(_java_enum_outline(node, source_bytes))
        elif node.type == 'method_declaration':
            outline.append(_java_method_entry(node, source_bytes))
    return outline


def _java_class_outline(node, source_bytes: bytes) -> dict:
    """Build outline entry for a Java class."""
    name_node = _find_child_by_type(node, 'identifier')
    name = _node_text(name_node, source_bytes) if name_node else '?'

    mods = _java_get_modifiers(node, source_bytes)

    # Superclass
    superclass_node = _find_child_by_type(node, 'superclass')
    extends = ''
    if superclass_node:
        type_node = _find_child_by_type(superclass_node, 'type_identifier', 'generic_type', 'scoped_type_identifier')
        if type_node:
            extends = f" extends {_node_text(type_node, source_bytes)}"

    # Interfaces
    ifaces_node = _find_child_by_type(node, 'super_interfaces')
    implements = ''
    if ifaces_node:
        tl = _find_child_by_type(ifaces_node, 'type_list')
        if tl:
            implements = f" implements {_node_text(tl, source_bytes)}"

    entry = {
        'kind': 'class',
        'name': name,
        'bases': f"{extends}{implements}".strip(),
        'modifiers': mods,
        'start': node.start_point[0] + 1,
        'end': node.end_point[0] + 1,
        'methods': [],
    }

    body = _find_child_by_type(node, 'class_body')
    if body:
        for child in body.children:
            if child.type == 'method_declaration':
                entry['methods'].append(_java_method_entry(child, source_bytes))
            elif child.type == 'constructor_declaration':
                entry['methods'].append(_java_constructor_entry(child, source_bytes))
            elif child.type == 'field_declaration':
                entry['methods'].append(_java_field_entry(child, source_bytes))
            elif child.type == 'class_declaration':
                entry['methods'].append({
                    'name': _node_text(_find_child_by_type(child, 'identifier'), source_bytes),
                    'sig': f"class {_node_text(_find_child_by_type(child, 'identifier'), source_bytes)}",
                    'start': child.start_point[0] + 1,
                    'end': child.end_point[0] + 1,
                })
            elif child.type == 'interface_declaration':
                entry['methods'].append({
                    'name': _node_text(_find_child_by_type(child, 'identifier'), source_bytes),
                    'sig': f"interface {_node_text(_find_child_by_type(child, 'identifier'), source_bytes)}",
                    'start': child.start_point[0] + 1,
                    'end': child.end_point[0] + 1,
                })
            elif child.type == 'enum_declaration':
                entry['methods'].append({
                    'name': _node_text(_find_child_by_type(child, 'identifier'), source_bytes),
                    'sig': f"enum {_node_text(_find_child_by_type(child, 'identifier'), source_bytes)}",
                    'start': child.start_point[0] + 1,
                    'end': child.end_point[0] + 1,
                })
    return entry


def _java_interface_outline(node, source_bytes: bytes) -> dict:
    """Build outline entry for a Java interface."""
    name_node = _find_child_by_type(node, 'identifier')
    name = _node_text(name_node, source_bytes) if name_node else '?'
    mods = _java_get_modifiers(node, source_bytes)

    entry = {
        'kind': 'interface',
        'name': name,
        'bases': '',
        'modifiers': mods,
        'start': node.start_point[0] + 1,
        'end': node.end_point[0] + 1,
        'methods': [],
    }

    body = _find_child_by_type(node, 'interface_body')
    if body:
        for child in body.children:
            if child.type == 'method_declaration':
                entry['methods'].append(_java_method_entry(child, source_bytes))
            elif child.type == 'constant_declaration':
                entry['methods'].append(_java_field_entry(child, source_bytes))
    return entry


def _java_enum_outline(node, source_bytes: bytes) -> dict:
    """Build outline entry for a Java enum."""
    name_node = _find_child_by_type(node, 'identifier')
    name = _node_text(name_node, source_bytes) if name_node else '?'

    entry = {
        'kind': 'enum',
        'name': name,
        'bases': '',
        'modifiers': _java_get_modifiers(node, source_bytes),
        'start': node.start_point[0] + 1,
        'end': node.end_point[0] + 1,
        'methods': [],
    }

    body = _find_child_by_type(node, 'enum_body')
    if body:
        for child in body.children:
            if child.type == 'method_declaration':
                entry['methods'].append(_java_method_entry(child, source_bytes))
            elif child.type == 'constructor_declaration':
                entry['methods'].append(_java_constructor_entry(child, source_bytes))
            elif child.type == 'enum_constant':
                cname = _find_child_by_type(child, 'identifier')
                entry['methods'].append({
                    'name': _node_text(cname, source_bytes) if cname else '?',
                    'sig': _node_text(cname, source_bytes) if cname else '?',
                    'start': child.start_point[0] + 1,
                    'end': child.end_point[0] + 1,
                })
    return entry


def _java_method_entry(node, source_bytes: bytes) -> dict:
    """Build outline entry for a Java method."""
    name_node = _find_child_by_type(node, 'identifier')
    name = _node_text(name_node, source_bytes) if name_node else '?'
    mods = _java_get_modifiers(node, source_bytes)
    ret_type = _java_get_return_type(node, source_bytes)
    params = _java_format_params(node, source_bytes)
    mod_str = f"{mods} " if mods else ''
    ret_str = f"{ret_type} " if ret_type else ''
    return {
        'name': name,
        'sig': f"{mod_str}{ret_str}{name}{params}",
        'start': node.start_point[0] + 1,
        'end': node.end_point[0] + 1,
    }


def _java_constructor_entry(node, source_bytes: bytes) -> dict:
    """Build outline entry for a Java constructor."""
    name_node = _find_child_by_type(node, 'identifier')
    name = _node_text(name_node, source_bytes) if name_node else '?'
    mods = _java_get_modifiers(node, source_bytes)
    params = _java_format_params(node, source_bytes)
    mod_str = f"{mods} " if mods else ''
    return {
        'name': name,
        'sig': f"{mod_str}{name}{params}",
        'start': node.start_point[0] + 1,
        'end': node.end_point[0] + 1,
    }


def _java_field_entry(node, source_bytes: bytes) -> dict:
    """Build outline entry for a Java field."""
    name_node = _find_child_by_type(node, 'variable_declarator')
    if name_node:
        ident = _find_child_by_type(name_node, 'identifier')
        name = _node_text(ident, source_bytes) if ident else '?'
    else:
        name = '?'
    mods = _java_get_modifiers(node, source_bytes)
    type_str = _java_get_return_type(node, source_bytes)  # reuse for field type
    mod_str = f"{mods} " if mods else ''
    type_disp = f"{type_str} " if type_str else ''
    return {
        'name': name,
        'sig': f"{mod_str}{type_disp}{name}",
        'start': node.start_point[0] + 1,
        'end': node.end_point[0] + 1,
        'kind': 'field',
    }


def _java_find_symbol(source: str, symbol_name: str) -> list:
    """Find all occurrences of a symbol in Java source."""
    parser = _get_parser('java')
    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)

    matches = []

    def visit(node, parent_class=None):
        if node.type in ('class_declaration', 'interface_declaration', 'enum_declaration'):
            name_node = _find_child_by_type(node, 'identifier')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                kind_map = {'class_declaration': 'class', 'interface_declaration': 'interface',
                            'enum_declaration': 'enum'}
                matches.append({
                    'kind': kind_map.get(node.type, node.type),
                    'name': name,
                    'start': node.start_point[0] + 1,
                    'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })
            # Recurse into body
            body = _find_child_by_type(node, 'class_body', 'interface_body', 'enum_body')
            if body:
                for child in body.children:
                    visit(child, name)
        elif node.type in ('method_declaration', 'constructor_declaration'):
            name_node = _find_child_by_type(node, 'identifier')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                matches.append({
                    'kind': 'method' if node.type == 'method_declaration' else 'constructor',
                    'name': name,
                    'start': node.start_point[0] + 1,
                    'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })
        else:
            for child in node.children:
                visit(child, parent_class)

    visit(tree.root_node)
    return matches


# ---------------------------------------------------------------------------
# JavaScript outline / symbol
# ---------------------------------------------------------------------------

def _js_format_params(node, source_bytes: bytes) -> str:
    """Format JS function parameters."""
    params_node = _find_child_by_type(node, 'formal_parameters')
    if not params_node:
        return '()'
    return _node_text(params_node, source_bytes)


def _js_outline(source: str) -> list:
    """Parse JavaScript source and return outline entries."""
    parser = _get_parser('javascript')
    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)
    root = tree.root_node
    outline = []

    for node in root.children:
        entries = _js_node_to_outline(node, source_bytes)
        outline.extend(entries)
    return outline


def _js_node_to_outline(node, source_bytes: bytes) -> list:
    """Convert a top-level JS node to outline entries."""
    results = []

    if node.type == 'class_declaration':
        results.append(_js_class_outline(node, source_bytes))

    elif node.type == 'function_declaration':
        name_node = _find_child_by_type(node, 'identifier')
        name = _node_text(name_node, source_bytes) if name_node else '?'
        params = _js_format_params(node, source_bytes)
        is_async = any(c.type == 'async' for c in node.children)
        prefix = 'async ' if is_async else ''
        results.append({
            'kind': 'function',
            'name': name,
            'sig': f"{prefix}function {name}{params}",
            'start': node.start_point[0] + 1,
            'end': node.end_point[0] + 1,
        })

    elif node.type in ('lexical_declaration', 'variable_declaration'):
        for child in node.children:
            if child.type == 'variable_declarator':
                ident = _find_child_by_type(child, 'identifier')
                if not ident:
                    continue
                name = _node_text(ident, source_bytes)
                value_node = None
                for c in child.children:
                    if c.type not in ('identifier', '='):
                        value_node = c
                        break
                if value_node and value_node.type == 'arrow_function':
                    params = _js_format_params(value_node, source_bytes)
                    is_async = any(c.type == 'async' for c in value_node.children)
                    prefix = 'async ' if is_async else ''
                    results.append({
                        'kind': 'function',
                        'name': name,
                        'sig': f"const {name} = {prefix}{params} =>",
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                    })
                elif value_node and value_node.type in ('function', 'function_expression'):
                    params = _js_format_params(value_node, source_bytes)
                    results.append({
                        'kind': 'function',
                        'name': name,
                        'sig': f"const {name} = function{params}",
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                    })
                elif value_node and value_node.type == 'class':
                    results.append(_js_class_outline(value_node, source_bytes, name_override=name))
                else:
                    results.append({
                        'kind': 'variable',
                        'name': name,
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                    })

    elif node.type == 'export_statement':
        for child in node.children:
            if child.type in ('class_declaration', 'function_declaration',
                              'lexical_declaration', 'variable_declaration'):
                results.extend(_js_node_to_outline(child, source_bytes))

    return results


def _js_class_outline(node, source_bytes: bytes, name_override: str | None = None) -> dict:
    """Build outline entry for a JS class."""
    name_node = _find_child_by_type(node, 'identifier')
    name = name_override or (_node_text(name_node, source_bytes) if name_node else '?')

    # Heritage (extends)
    heritage = _find_child_by_type(node, 'class_heritage')
    extends = ''
    if heritage:
        ext_ident = _find_child_by_type(heritage, 'identifier', 'member_expression')
        if ext_ident:
            extends = f" extends {_node_text(ext_ident, source_bytes)}"

    entry = {
        'kind': 'class',
        'name': name,
        'bases': extends.strip(),
        'start': node.start_point[0] + 1,
        'end': node.end_point[0] + 1,
        'methods': [],
    }

    body = _find_child_by_type(node, 'class_body')
    if body:
        for child in body.children:
            if child.type == 'method_definition':
                m_name_node = _find_child_by_type(child, 'property_identifier', 'computed_property_name')
                m_name = _node_text(m_name_node, source_bytes) if m_name_node else '?'
                params = _js_format_params(child, source_bytes)
                # Check for static/async/get/set
                prefixes = []
                for c in child.children:
                    if c.type == 'static':
                        prefixes.append('static')
                    elif c.type == 'async':
                        prefixes.append('async')
                    elif c.type == 'get':
                        prefixes.append('get')
                    elif c.type == 'set':
                        prefixes.append('set')
                prefix = ' '.join(prefixes)
                prefix = f"{prefix} " if prefix else ''
                entry['methods'].append({
                    'name': m_name,
                    'sig': f"{prefix}{m_name}{params}",
                    'start': child.start_point[0] + 1,
                    'end': child.end_point[0] + 1,
                })
            elif child.type == 'field_definition':
                f_name_node = _find_child_by_type(child, 'property_identifier')
                if f_name_node:
                    entry['methods'].append({
                        'name': _node_text(f_name_node, source_bytes),
                        'sig': _node_text(f_name_node, source_bytes),
                        'start': child.start_point[0] + 1,
                        'end': child.end_point[0] + 1,
                        'kind': 'field',
                    })
    return entry


def _js_find_symbol(source: str, symbol_name: str) -> list:
    """Find all occurrences of a symbol in JavaScript source."""
    parser = _get_parser('javascript')
    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)

    matches = []

    def visit(node, parent_class=None):
        if node.type == 'class_declaration':
            name_node = _find_child_by_type(node, 'identifier')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                matches.append({
                    'kind': 'class',
                    'name': name,
                    'start': node.start_point[0] + 1,
                    'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })
            body = _find_child_by_type(node, 'class_body')
            if body:
                for child in body.children:
                    visit(child, name)

        elif node.type == 'function_declaration':
            name_node = _find_child_by_type(node, 'identifier')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                is_async = any(c.type == 'async' for c in node.children)
                matches.append({
                    'kind': 'async_function' if is_async else 'function',
                    'name': name,
                    'start': node.start_point[0] + 1,
                    'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })

        elif node.type == 'method_definition':
            name_node = _find_child_by_type(node, 'property_identifier', 'computed_property_name')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                matches.append({
                    'kind': 'method',
                    'name': name,
                    'start': node.start_point[0] + 1,
                    'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })

        elif node.type in ('lexical_declaration', 'variable_declaration'):
            for child in node.children:
                if child.type == 'variable_declarator':
                    ident = _find_child_by_type(child, 'identifier')
                    if ident and _node_text(ident, source_bytes) == symbol_name:
                        # Check if it's an arrow function or function expression
                        value_node = None
                        for c in child.children:
                            if c.type not in ('identifier', '='):
                                value_node = c
                                break
                        if value_node and value_node.type in ('arrow_function', 'function', 'function_expression'):
                            kind = 'function'
                        elif value_node and value_node.type == 'class':
                            kind = 'class'
                        else:
                            kind = 'variable'
                        matches.append({
                            'kind': kind,
                            'name': symbol_name,
                            'start': node.start_point[0] + 1,
                            'end': node.end_point[0] + 1,
                            'parent_class': parent_class,
                        })

        elif node.type == 'export_statement':
            for child in node.children:
                visit(child, parent_class)
        else:
            for child in node.children:
                visit(child, parent_class)

    visit(tree.root_node)
    return matches


# ---------------------------------------------------------------------------
# Go outline / symbol
# ---------------------------------------------------------------------------

def _go_format_params(node, source_bytes: bytes) -> str:
    """Format Go function parameters."""
    params = _find_child_by_type(node, 'parameter_list')
    if not params:
        return '()'
    return _node_text(params, source_bytes)


def _go_get_return_type(node, source_bytes: bytes) -> str:
    """Get return type(s) for a Go function."""
    # Return type comes after the parameter list
    found_params = 0
    for child in node.children:
        if child.type == 'parameter_list':
            found_params += 1
            if found_params == 2:
                # Second parameter_list is actually return types (e.g. (int, error))
                return _node_text(child, source_bytes)
        elif found_params >= 1 and child.type in ('type_identifier', 'pointer_type',
                'slice_type', 'map_type', 'interface_type', 'struct_type',
                'qualified_type', 'array_type', 'channel_type', 'function_type'):
            return _node_text(child, source_bytes)
    return ''


def _go_outline(source: str) -> list:
    """Parse Go source and return outline entries."""
    parser = _get_parser('go')
    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)
    outline = []

    for node in tree.root_node.children:
        if node.type == 'function_declaration':
            name_node = _find_child_by_type(node, 'identifier')
            name = _node_text(name_node, source_bytes) if name_node else '?'
            params = _go_format_params(node, source_bytes)
            ret = _go_get_return_type(node, source_bytes)
            ret_str = f" {ret}" if ret else ''
            outline.append({
                'kind': 'function',
                'name': name,
                'sig': f"func {name}{params}{ret_str}",
                'start': node.start_point[0] + 1,
                'end': node.end_point[0] + 1,
            })
        elif node.type == 'method_declaration':
            # func (receiver) Name(params) returnType
            name_node = _find_child_by_type(node, 'field_identifier')
            name = _node_text(name_node, source_bytes) if name_node else '?'
            # Get receiver and params — parameter_lists appear as:
            # [0] = receiver, [1] = params, [2] = return params (if tuple return)
            param_lists = _find_children_by_type(node, 'parameter_list')
            receiver = _node_text(param_lists[0], source_bytes) if param_lists else ''
            # Params come after the field_identifier
            found_name = False
            params = '()'
            ret = ''
            for child in node.children:
                if child.type == 'field_identifier':
                    found_name = True
                elif found_name and child.type == 'parameter_list':
                    params = _node_text(child, source_bytes)
                    found_name = False  # next param_list would be return
                elif child.type in ('type_identifier', 'pointer_type', 'slice_type',
                        'map_type', 'qualified_type', 'array_type'):
                    ret = _node_text(child, source_bytes)
            ret_str = f" {ret}" if ret else ''
            # Extract receiver type name
            recv_type = ''
            if param_lists:
                for c in param_lists[0].children:
                    if c.type == 'parameter_declaration':
                        for cc in c.children:
                            if cc.type in ('type_identifier', 'pointer_type'):
                                recv_type = _node_text(cc, source_bytes).lstrip('*')
                                break
            outline.append({
                'kind': 'function',
                'name': name,
                'sig': f"func {receiver} {name}{params}{ret_str}",
                'start': node.start_point[0] + 1,
                'end': node.end_point[0] + 1,
                'receiver': recv_type,
            })
        elif node.type == 'type_declaration':
            for spec in node.children:
                if spec.type == 'type_spec':
                    name_node = _find_child_by_type(spec, 'type_identifier')
                    name = _node_text(name_node, source_bytes) if name_node else '?'
                    body = _find_child_by_type(spec, 'struct_type', 'interface_type')
                    if body and body.type == 'struct_type':
                        entry = {
                            'kind': 'class',  # struct as class for consistency
                            'name': name,
                            'bases': 'struct',
                            'start': node.start_point[0] + 1,
                            'end': node.end_point[0] + 1,
                            'methods': [],
                        }
                        # Add fields
                        field_list = _find_child_by_type(body, 'field_declaration_list')
                        if field_list:
                            for field in field_list.children:
                                if field.type == 'field_declaration':
                                    fname = _find_child_by_type(field, 'field_identifier')
                                    ftype = _find_child_by_type(field, 'type_identifier', 'pointer_type',
                                                                 'slice_type', 'map_type', 'qualified_type')
                                    if fname:
                                        type_str = _node_text(ftype, source_bytes) if ftype else ''
                                        entry['methods'].append({
                                            'name': _node_text(fname, source_bytes),
                                            'sig': f"{_node_text(fname, source_bytes)} {type_str}",
                                            'start': field.start_point[0] + 1,
                                            'end': field.end_point[0] + 1,
                                            'kind': 'field',
                                        })
                        outline.append(entry)
                    elif body and body.type == 'interface_type':
                        entry = {
                            'kind': 'interface',
                            'name': name,
                            'bases': '',
                            'start': node.start_point[0] + 1,
                            'end': node.end_point[0] + 1,
                            'methods': [],
                        }
                        for child in body.children:
                            if child.type == 'method_spec':
                                mname = _find_child_by_type(child, 'field_identifier')
                                if mname:
                                    entry['methods'].append({
                                        'name': _node_text(mname, source_bytes),
                                        'sig': _node_text(child, source_bytes).strip(),
                                        'start': child.start_point[0] + 1,
                                        'end': child.end_point[0] + 1,
                                    })
                        outline.append(entry)
                    else:
                        # Type alias
                        outline.append({
                            'kind': 'variable',
                            'name': name,
                            'start': node.start_point[0] + 1,
                            'end': node.end_point[0] + 1,
                        })

    # Group methods with their receiver structs
    struct_map = {e['name']: e for e in outline if e['kind'] == 'class'}
    remaining = []
    for e in outline:
        recv = e.get('receiver', '')
        if recv and recv in struct_map:
            struct_map[recv]['methods'].append({
                'name': e['name'],
                'sig': e['sig'],
                'start': e['start'],
                'end': e['end'],
            })
        else:
            remaining.append(e)
    return remaining


def _go_find_symbol(source: str, symbol_name: str) -> list:
    """Find all occurrences of a symbol in Go source."""
    parser = _get_parser('go')
    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)
    matches = []

    for node in tree.root_node.children:
        if node.type == 'function_declaration':
            name_node = _find_child_by_type(node, 'identifier')
            if name_node and _node_text(name_node, source_bytes) == symbol_name:
                matches.append({
                    'kind': 'function',
                    'name': symbol_name,
                    'start': node.start_point[0] + 1,
                    'end': node.end_point[0] + 1,
                    'parent_class': None,
                })
        elif node.type == 'method_declaration':
            name_node = _find_child_by_type(node, 'field_identifier')
            if name_node and _node_text(name_node, source_bytes) == symbol_name:
                # Get receiver type
                param_lists = _find_children_by_type(node, 'parameter_list')
                recv_type = None
                if param_lists:
                    for c in param_lists[0].children:
                        if c.type == 'parameter_declaration':
                            for cc in c.children:
                                if cc.type in ('type_identifier', 'pointer_type'):
                                    recv_type = _node_text(cc, source_bytes).lstrip('*')
                matches.append({
                    'kind': 'method',
                    'name': symbol_name,
                    'start': node.start_point[0] + 1,
                    'end': node.end_point[0] + 1,
                    'parent_class': recv_type,
                })
        elif node.type == 'type_declaration':
            for spec in node.children:
                if spec.type == 'type_spec':
                    name_node = _find_child_by_type(spec, 'type_identifier')
                    if name_node and _node_text(name_node, source_bytes) == symbol_name:
                        body = _find_child_by_type(spec, 'struct_type', 'interface_type')
                        kind = 'class' if body and body.type == 'struct_type' else \
                               'interface' if body and body.type == 'interface_type' else 'type'
                        matches.append({
                            'kind': kind,
                            'name': symbol_name,
                            'start': node.start_point[0] + 1,
                            'end': node.end_point[0] + 1,
                            'parent_class': None,
                        })
    return matches


# ---------------------------------------------------------------------------
# TypeScript outline / symbol (extends JavaScript with type annotations)
# ---------------------------------------------------------------------------

def _ts_outline(source: str) -> list:
    """Parse TypeScript source and return outline entries.
    TypeScript is similar to JavaScript but adds interfaces, type annotations, enums.
    """
    parser = _get_parser('typescript')
    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)
    outline = []

    for node in tree.root_node.children:
        entries = _ts_node_to_outline(node, source_bytes)
        outline.extend(entries)
    return outline


def _ts_node_to_outline(node, source_bytes: bytes) -> list:
    """Convert a top-level TS node to outline entries."""
    results = []

    if node.type == 'class_declaration':
        results.append(_ts_class_outline(node, source_bytes))

    elif node.type == 'interface_declaration':
        name_node = _find_child_by_type(node, 'type_identifier')
        name = _node_text(name_node, source_bytes) if name_node else '?'
        entry = {
            'kind': 'interface',
            'name': name,
            'bases': '',
            'start': node.start_point[0] + 1,
            'end': node.end_point[0] + 1,
            'methods': [],
        }
        body = _find_child_by_type(node, 'interface_body', 'object_type')
        if body:
            for child in body.children:
                if child.type in ('method_signature', 'property_signature'):
                    sig_name = _find_child_by_type(child, 'property_identifier')
                    if sig_name:
                        entry['methods'].append({
                            'name': _node_text(sig_name, source_bytes),
                            'sig': _node_text(child, source_bytes).strip().rstrip(';'),
                            'start': child.start_point[0] + 1,
                            'end': child.end_point[0] + 1,
                        })
        results.append(entry)

    elif node.type == 'enum_declaration':
        name_node = _find_child_by_type(node, 'identifier')
        name = _node_text(name_node, source_bytes) if name_node else '?'
        entry = {
            'kind': 'enum',
            'name': name,
            'bases': '',
            'start': node.start_point[0] + 1,
            'end': node.end_point[0] + 1,
            'methods': [],
        }
        body = _find_child_by_type(node, 'enum_body')
        if body:
            for child in body.children:
                if child.type == 'enum_assignment':
                    ename = _find_child_by_type(child, 'property_identifier')
                    if ename:
                        entry['methods'].append({
                            'name': _node_text(ename, source_bytes),
                            'sig': _node_text(ename, source_bytes),
                            'start': child.start_point[0] + 1,
                            'end': child.end_point[0] + 1,
                        })
        results.append(entry)

    elif node.type == 'function_declaration':
        name_node = _find_child_by_type(node, 'identifier')
        name = _node_text(name_node, source_bytes) if name_node else '?'
        params = _find_child_by_type(node, 'formal_parameters')
        params_str = _node_text(params, source_bytes) if params else '()'
        ret = _find_child_by_type(node, 'type_annotation')
        ret_str = _node_text(ret, source_bytes) if ret else ''
        is_async = any(c.type == 'async' for c in node.children)
        prefix = 'async ' if is_async else ''
        results.append({
            'kind': 'function',
            'name': name,
            'sig': f"{prefix}function {name}{params_str}{ret_str}",
            'start': node.start_point[0] + 1,
            'end': node.end_point[0] + 1,
        })

    elif node.type in ('lexical_declaration', 'variable_declaration'):
        for child in node.children:
            if child.type == 'variable_declarator':
                ident = _find_child_by_type(child, 'identifier')
                if not ident:
                    continue
                name = _node_text(ident, source_bytes)
                value = None
                for c in child.children:
                    if c.type in ('arrow_function', 'function', 'function_expression'):
                        value = c
                        break
                if value:
                    params = _find_child_by_type(value, 'formal_parameters')
                    params_str = _node_text(params, source_bytes) if params else '()'
                    is_async = any(c.type == 'async' for c in value.children)
                    prefix = 'async ' if is_async else ''
                    results.append({
                        'kind': 'function',
                        'name': name,
                        'sig': f"const {name} = {prefix}{params_str} =>",
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                    })
                else:
                    results.append({
                        'kind': 'variable',
                        'name': name,
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                    })

    elif node.type == 'type_alias_declaration':
        name_node = _find_child_by_type(node, 'type_identifier')
        if name_node:
            results.append({
                'kind': 'variable',
                'name': _node_text(name_node, source_bytes),
                'start': node.start_point[0] + 1,
                'end': node.end_point[0] + 1,
            })

    elif node.type == 'export_statement':
        for child in node.children:
            if child.type in ('class_declaration', 'function_declaration', 'interface_declaration',
                              'enum_declaration', 'lexical_declaration', 'variable_declaration',
                              'type_alias_declaration'):
                results.extend(_ts_node_to_outline(child, source_bytes))

    return results


def _ts_class_outline(node, source_bytes: bytes) -> dict:
    """Build outline entry for a TypeScript class."""
    name_node = _find_child_by_type(node, 'type_identifier')
    name = _node_text(name_node, source_bytes) if name_node else '?'

    heritage = _find_child_by_type(node, 'class_heritage')
    extends = ''
    if heritage:
        ext = _find_child_by_type(heritage, 'extends_clause')
        if ext:
            extends = _node_text(ext, source_bytes).replace('extends ', '')

    entry = {
        'kind': 'class',
        'name': name,
        'bases': extends,
        'start': node.start_point[0] + 1,
        'end': node.end_point[0] + 1,
        'methods': [],
    }

    body = _find_child_by_type(node, 'class_body')
    if body:
        for child in body.children:
            if child.type == 'method_definition':
                m_name = _find_child_by_type(child, 'property_identifier')
                if not m_name:
                    continue
                params = _find_child_by_type(child, 'formal_parameters')
                params_str = _node_text(params, source_bytes) if params else '()'
                ret = _find_child_by_type(child, 'type_annotation')
                ret_str = _node_text(ret, source_bytes) if ret else ''
                prefixes = []
                for c in child.children:
                    if c.type in ('public', 'private', 'protected', 'static', 'async', 'readonly'):
                        prefixes.append(c.type)
                    elif c.type == 'override_modifier':
                        prefixes.append('override')
                prefix = ' '.join(prefixes)
                prefix = f"{prefix} " if prefix else ''
                entry['methods'].append({
                    'name': _node_text(m_name, source_bytes),
                    'sig': f"{prefix}{_node_text(m_name, source_bytes)}{params_str}{ret_str}",
                    'start': child.start_point[0] + 1,
                    'end': child.end_point[0] + 1,
                })
            elif child.type == 'public_field_definition':
                f_name = _find_child_by_type(child, 'property_identifier')
                if f_name:
                    entry['methods'].append({
                        'name': _node_text(f_name, source_bytes),
                        'sig': _node_text(child, source_bytes).strip().rstrip(';'),
                        'start': child.start_point[0] + 1,
                        'end': child.end_point[0] + 1,
                        'kind': 'field',
                    })
    return entry


def _ts_find_symbol(source: str, symbol_name: str) -> list:
    """Find all occurrences of a symbol in TypeScript source."""
    parser = _get_parser('typescript')
    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)
    matches = []

    def visit(node, parent_class=None):
        if node.type == 'class_declaration':
            name_node = _find_child_by_type(node, 'type_identifier')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                matches.append({
                    'kind': 'class', 'name': name,
                    'start': node.start_point[0] + 1, 'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })
            body = _find_child_by_type(node, 'class_body')
            if body:
                for child in body.children:
                    visit(child, name)

        elif node.type == 'interface_declaration':
            name_node = _find_child_by_type(node, 'type_identifier')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                matches.append({
                    'kind': 'interface', 'name': name,
                    'start': node.start_point[0] + 1, 'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })

        elif node.type == 'enum_declaration':
            name_node = _find_child_by_type(node, 'identifier')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                matches.append({
                    'kind': 'enum', 'name': name,
                    'start': node.start_point[0] + 1, 'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })

        elif node.type == 'function_declaration':
            name_node = _find_child_by_type(node, 'identifier')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                is_async = any(c.type == 'async' for c in node.children)
                matches.append({
                    'kind': 'async_function' if is_async else 'function', 'name': name,
                    'start': node.start_point[0] + 1, 'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })

        elif node.type == 'method_definition':
            name_node = _find_child_by_type(node, 'property_identifier')
            name = _node_text(name_node, source_bytes) if name_node else ''
            if name == symbol_name:
                matches.append({
                    'kind': 'method', 'name': name,
                    'start': node.start_point[0] + 1, 'end': node.end_point[0] + 1,
                    'parent_class': parent_class,
                })

        elif node.type in ('lexical_declaration', 'variable_declaration'):
            for child in node.children:
                if child.type == 'variable_declarator':
                    ident = _find_child_by_type(child, 'identifier')
                    if ident and _node_text(ident, source_bytes) == symbol_name:
                        value = None
                        for c in child.children:
                            if c.type in ('arrow_function', 'function', 'function_expression'):
                                value = c
                                break
                        kind = 'function' if value else 'variable'
                        matches.append({
                            'kind': kind, 'name': symbol_name,
                            'start': node.start_point[0] + 1, 'end': node.end_point[0] + 1,
                            'parent_class': parent_class,
                        })

        elif node.type == 'export_statement':
            for child in node.children:
                visit(child, parent_class)
        else:
            for child in node.children:
                visit(child, parent_class)

    visit(tree.root_node)
    return matches


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_language_for_file(path: str) -> str | None:
    """Return language name if file is supported by tree-sitter, else None."""
    import os
    _, ext = os.path.splitext(path.rstrip('/'))
    return TREESITTER_EXTENSIONS.get(ext.lower())


def treesitter_outline(source: str, lang: str) -> list:
    """Return outline entries for given source in the specified language.

    Returns list of dicts with keys: kind, name, start, end, and optionally
    bases, methods (for classes), sig (for functions/methods).
    """
    dispatch = {
        'java': _java_outline,
        'javascript': _js_outline,
        'go': _go_outline,
        'typescript': _ts_outline,
    }
    fn = dispatch.get(lang)
    if not fn:
        raise ValueError(f"Unsupported language: {lang}")
    return fn(source)


def treesitter_find_symbol(source: str, lang: str, symbol_name: str) -> list:
    """Find symbol in source. Returns list of match dicts with
    kind, name, start, end, parent_class.
    """
    dispatch = {
        'java': _java_find_symbol,
        'javascript': _js_find_symbol,
        'go': _go_find_symbol,
        'typescript': _ts_find_symbol,
    }
    fn = dispatch.get(lang)
    if not fn:
        raise ValueError(f"Unsupported language: {lang}")
    return fn(source, symbol_name)


def format_outline(outline: list, path: str, total_lines: int) -> str:
    """Format outline entries into display string (same format as Python view_outline)."""
    lines = [f"[{path}] outline ({total_lines} lines total)", ""]
    for entry in outline:
        kind = entry['kind']
        if kind in ('class', 'interface', 'enum'):
            name = entry['name']
            bases = entry.get('bases', '')
            bases_str = f"({bases})" if bases else '' if kind == 'class' else ''
            if kind == 'interface':
                bases_str = f"({bases})" if bases else ''
            start, end = entry['start'], entry['end']
            mods = entry.get('modifiers', '')
            mod_str = f"{mods} " if mods else ''
            lines.append(f"  {mod_str}{kind} {name}{bases_str}  (L{start}-{end})")
            for method in entry.get('methods', []):
                m_start, m_end = method['start'], method['end']
                lines.append(f"    {method['sig']}  (L{m_start}-{m_end})")
            lines.append("")
        elif kind == 'function':
            start, end = entry['start'], entry['end']
            sig = entry.get('sig', f"function {entry['name']}")
            lines.append(f"  {sig}  (L{start}-{end})")
        elif kind in ('variable', 'field'):
            lines.append(f"  {entry['name']} = ...  (L{entry['start']})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Syntax checking
# ---------------------------------------------------------------------------

def treesitter_syntax_check(source: str, lang: str) -> str | None:
    """Check syntax of source code using tree-sitter.
    Returns error string if syntax errors found, None if valid.
    """
    try:
        parser = _get_parser(lang)
    except ValueError:
        return None  # unsupported language, skip check

    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)

    errors = []
    def find_errors(node, depth=0):
        if node.type == 'ERROR' or node.is_missing:
            line = node.start_point[0] + 1
            col = node.start_point[1] + 1
            snippet = source_bytes[node.start_byte:min(node.end_byte, node.start_byte + 40)].decode('utf-8', errors='replace')
            errors.append(f"  Line {line}, col {col}: syntax error near '{snippet}'")
        if depth < 50:  # prevent infinite recursion on deeply nested errors
            for child in node.children:
                find_errors(child, depth + 1)

    find_errors(tree.root_node)

    if errors:
        return f"[SYNTAX ERROR] {len(errors)} error(s) found:\n" + "\n".join(errors[:10])
    return None


# ---------------------------------------------------------------------------
# Call chain tracing
# ---------------------------------------------------------------------------

def _extract_calls_from_node(node, source_bytes: bytes) -> set:
    """Extract all function/method call names from a tree-sitter node recursively."""
    calls = set()

    def visit(n):
        if n.type == 'call_expression':
            # JavaScript/Go: func(), obj.method()
            func_node = n.children[0] if n.children else None
            if func_node:
                if func_node.type == 'identifier':
                    calls.add(_node_text(func_node, source_bytes))
                elif func_node.type == 'member_expression':
                    # JS: obj.method
                    prop = _find_child_by_type(func_node, 'property_identifier')
                    if prop:
                        calls.add(_node_text(prop, source_bytes))
                elif func_node.type == 'selector_expression':
                    # Go: pkg.Function or obj.Method
                    field = _find_child_by_type(func_node, 'field_identifier')
                    if field:
                        calls.add(_node_text(field, source_bytes))
        elif n.type == 'method_invocation':
            # Java: obj.method() or method()
            name_node = _find_child_by_type(n, 'identifier')
            if name_node:
                calls.add(_node_text(name_node, source_bytes))
        elif n.type == 'object_creation_expression':
            # Java: new ClassName()
            type_node = _find_child_by_type(n, 'type_identifier')
            if type_node:
                calls.add(_node_text(type_node, source_bytes))
        elif n.type == 'new_expression':
            # JavaScript: new ClassName()
            ctor = n.children[1] if len(n.children) > 1 else None
            if ctor and ctor.type == 'identifier':
                calls.add(_node_text(ctor, source_bytes))

        for child in n.children:
            visit(child)

    visit(node)
    return calls


def _build_call_index(source: str, lang: str) -> dict:
    """Build a function/method -> set of called functions index from source.

    Returns dict: { 'function_name': { 'file': str, 'start': int, 'end': int,
                                        'parent': str|None, 'calls': set[str] } }
    """
    parser = _get_parser(lang)
    source_bytes = source.encode('utf-8')
    tree = parser.parse(source_bytes)
    index = {}

    def visit(node, parent_class=None):
        if lang == 'java':
            if node.type in ('method_declaration', 'constructor_declaration'):
                name_node = _find_child_by_type(node, 'identifier')
                if name_node:
                    name = _node_text(name_node, source_bytes)
                    key = f"{parent_class}.{name}" if parent_class else name
                    body = _find_child_by_type(node, 'block')
                    calls = _extract_calls_from_node(body, source_bytes) if body else set()
                    index[key] = {
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                        'parent': parent_class,
                        'calls': calls,
                    }
            elif node.type in ('class_declaration', 'interface_declaration', 'enum_declaration'):
                name_node = _find_child_by_type(node, 'identifier')
                cls_name = _node_text(name_node, source_bytes) if name_node else None
                body = _find_child_by_type(node, 'class_body', 'interface_body', 'enum_body')
                if body:
                    for child in body.children:
                        visit(child, cls_name)
                return  # don't recurse children again

        elif lang in ('javascript', 'typescript'):
            if node.type == 'function_declaration':
                name_node = _find_child_by_type(node, 'identifier')
                if name_node:
                    name = _node_text(name_node, source_bytes)
                    body = _find_child_by_type(node, 'statement_block')
                    calls = _extract_calls_from_node(body, source_bytes) if body else set()
                    index[name] = {
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                        'parent': parent_class,
                        'calls': calls,
                    }
            elif node.type == 'method_definition':
                name_node = _find_child_by_type(node, 'property_identifier')
                if name_node:
                    name = _node_text(name_node, source_bytes)
                    key = f"{parent_class}.{name}" if parent_class else name
                    body = _find_child_by_type(node, 'statement_block')
                    calls = _extract_calls_from_node(body, source_bytes) if body else set()
                    index[key] = {
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                        'parent': parent_class,
                        'calls': calls,
                    }
            elif node.type == 'class_declaration':
                name_node = _find_child_by_type(node, 'identifier', 'type_identifier')
                cls_name = _node_text(name_node, source_bytes) if name_node else None
                body = _find_child_by_type(node, 'class_body')
                if body:
                    for child in body.children:
                        visit(child, cls_name)
                return
            elif node.type in ('lexical_declaration', 'variable_declaration'):
                for child in node.children:
                    if child.type == 'variable_declarator':
                        ident = _find_child_by_type(child, 'identifier')
                        if not ident:
                            continue
                        name = _node_text(ident, source_bytes)
                        value = None
                        for c in child.children:
                            if c.type in ('arrow_function', 'function', 'function_expression'):
                                value = c
                                break
                        if value:
                            body = _find_child_by_type(value, 'statement_block')
                            if not body:
                                body = value
                            calls = _extract_calls_from_node(body, source_bytes)
                            index[name] = {
                                'start': node.start_point[0] + 1,
                                'end': node.end_point[0] + 1,
                                'parent': parent_class,
                                'calls': calls,
                            }

        elif lang == 'go':
            if node.type == 'function_declaration':
                name_node = _find_child_by_type(node, 'identifier')
                if name_node:
                    name = _node_text(name_node, source_bytes)
                    body = _find_child_by_type(node, 'block')
                    calls = _extract_calls_from_node(body, source_bytes) if body else set()
                    index[name] = {
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                        'parent': None,
                        'calls': calls,
                    }
            elif node.type == 'method_declaration':
                name_node = _find_child_by_type(node, 'field_identifier')
                if name_node:
                    name = _node_text(name_node, source_bytes)
                    # Get receiver type
                    recv_type = None
                    param_lists = _find_children_by_type(node, 'parameter_list')
                    if param_lists:
                        for c in param_lists[0].children:
                            if c.type == 'parameter_declaration':
                                for cc in c.children:
                                    if cc.type in ('type_identifier', 'pointer_type'):
                                        recv_type = _node_text(cc, source_bytes).lstrip('*')
                    key = f"{recv_type}.{name}" if recv_type else name
                    body = _find_child_by_type(node, 'block')
                    calls = _extract_calls_from_node(body, source_bytes) if body else set()
                    index[key] = {
                        'start': node.start_point[0] + 1,
                        'end': node.end_point[0] + 1,
                        'parent': recv_type,
                        'calls': calls,
                    }

        for child in node.children:
            visit(child, parent_class)

    visit(tree.root_node)
    return index


def treesitter_trace_calls(sources: dict[str, str], lang: str, function_name: str,
                           max_depth: int = 3) -> dict:
    """Trace call chain for a function across multiple source files.

    Args:
        sources: dict of {filepath: source_code}
        lang: language name
        function_name: function to trace
        max_depth: max recursion depth

    Returns dict with 'callers' and 'callees' lists.
    """
    # Build index across all files
    full_index = {}
    for filepath, source in sources.items():
        try:
            file_index = _build_call_index(source, lang)
            for name, info in file_index.items():
                info['file'] = filepath
                full_index[name] = info
        except Exception as e:
            logger.warning(f"Failed to index {filepath}: {e}")

    # Find callees (what does function_name call?)
    callees = []
    def trace_callees(name, depth=0, visited=None):
        if visited is None:
            visited = set()
        if name in visited or depth > max_depth:
            return
        visited.add(name)

        info = full_index.get(name)
        if not info:
            # Try with class prefix
            for key, val in full_index.items():
                if key.endswith(f'.{name}'):
                    info = val
                    name = key
                    break
        if not info:
            return

        for callee in sorted(info['calls']):
            callee_info = full_index.get(callee)
            if not callee_info:
                for key, val in full_index.items():
                    if key.endswith(f'.{callee}'):
                        callee_info = val
                        callee = key
                        break

            callees.append({
                'name': callee,
                'called_by': name,
                'depth': depth + 1,
                'file': callee_info['file'] if callee_info else '(unknown)',
                'line': callee_info['start'] if callee_info else 0,
            })
            if callee_info:
                trace_callees(callee, depth + 1, visited)

    trace_callees(function_name)

    # Find callers (what calls function_name?)
    callers = []
    target_short = function_name.split('.')[-1]
    for name, info in full_index.items():
        if target_short in info['calls'] or function_name in info['calls']:
            callers.append({
                'name': name,
                'file': info['file'],
                'line': info['start'],
            })

    return {
        'function': function_name,
        'callers': callers,
        'callees': callees,
    }
