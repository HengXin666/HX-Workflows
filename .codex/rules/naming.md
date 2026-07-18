# Naming and type rules

- Use English ASCII identifiers; Chinese is allowed in user-facing text, documentation, and comments.
- Add Python type annotations to new or changed public functions. Preserve TypeScript `strict` mode and prefer `unknown` plus narrowing over `any`.
- Python modules and functions use `snake_case`; classes and React components use `PascalCase`; TypeScript variables and handlers use `camelCase`.
- Environment variable and secret names use `UPPER_SNAKE_CASE`; never log their values.
- Do not introduce a new public underscore-prefixed API. Keep public exports deliberate.
