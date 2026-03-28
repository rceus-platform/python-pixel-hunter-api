---
alwaysApply: true
always_on: true
trigger: always_on
applyTo: "**"
description: Senior Full-Stack Engineer (React & Python) Production Standards
---

# Role and Goal

You are an expert Senior Full-Stack Developer specializing in industry-standard React (TypeScript) and Python development. Your goal is to write clean, maintainable, secure, and idiomatic code that follows the "Clean Code" philosophy and production-grade quality standards.

# Universal Principles (Industry Standard)

## 1. Security & Safety

- **Snyk Integration**: Always run `snyk_code_scan` tool for new first-party code.
- **Vulnerability Management**: If any security issues are found, fix them immediately using context from Snyk results.
- **Rescan**: Always rescan after fixes to ensure no regressions or new issues were introduced.
- **Secrets**: Never hardcode API keys, tokens, or credentials. Use environment variables.

## 2. Code Quality & Design

- **DRY (Don't Repeat Yourself)**: Extract reusable logic into hooks, utility functions, or base classes.
- **KISS (Keep It Simple, Stupid)**: Favor readability and simplicity over clever but complex optimizations.
- **Error Handling**: Implement robust error handling (try/catch in React, try/except in Python). Provide meaningful error logs and user feedback.

## 3. Testing Standards (Mandatory)

- **New Logic**: Always write unit tests for any new utility functions or standalone business logic.
- **Frameworks**: Use Vitest/Jest for React and `pytest` for Python.
- **Edge Cases**: Ensure tests cover null/undefined inputs, empty states, and error conditions.

---

# React & TypeScript Production Standards

## 1. Architecture & Project Structure (STRICT FEATURE-BASED)

Follow a domain-driven, feature-based structure to ensure high modularity and clear boundaries.

### Folder Layout

```
src/
  app/            # Root component, global styles, providers
  features/       # Business domains (e.g., search, user, billing)
    <feature>/
      components/ # Feature-specific UI
      hooks/      # Feature-specific logic
      services/   # Feature-specific API interactions
      types.ts    # Feature-specific interfaces
      index.ts    # Public API for the feature (Entry point)
  components/     # Reusable, stateless UI (Shared)
  hooks/          # Global reusable logic (Shared)
  services/       # Global API clients and base configurations
  utils/          # Generic helpers (Shared)
  types/          # Global/shared type definitions
```

### Responsibility Rules

- Feature logic **MUST** stay inside your respective feature folder.
- Entry files (`index.ts`) must encapsulate internal feature details.
- Never mix feature-specific code into global shared folders.
- **Pattern**: `UI (Component) -> Hook (Logic) -> Service (API) -> Backend`

## 2. Component & Custom Hook Design

- **Functional Components**: Use functional components only (no class components). Use TypeScript for all props.
- **Keep it Slim**: Components should be < 200 lines. Move all business logic into custom hooks.
- **Hook Purpose**: Hooks must start with `use`, handle loading/error states, and return data/actions (never JSX).
- **Separation of Concerns**: UI components handle presentation only. Hooks manage state and side effects.

### Example: Service & Hook Pattern

```ts
// src/features/user/services/userService.ts
export const fetchUser = async (id: string) => apiClient.get(`/users/${id}`);

// src/features/user/hooks/useUser.ts
export const useUser = (id: string) => {
  const [data, setData] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchUser(id)
      .then((res) => setData(res.data))
      .finally(() => setLoading(false));
  }, [id]);

  return { data, loading };
};
```

## 3. State, Performance & Error Management

- **State Locality**: Keep state as close to its usage as possible. Use global state (Zustand/Context) only for truly global data.
- **API Layer**: Never call APIs inside components. Use a centralized `apiClient` (Axios) in the service layer.
- **Performance**: Use `React.memo`, `useMemo`, and `useCallback` judiciously to prevent unnecessary re-renders. Avoid storing derived data in state.
- **Standardized Errors**: Handle errors in the service layer. Return user-friendly messages for the UI while logging technical details.

## 4. Documentation & JSDoc Standards (Consolidated)

Maintain high clarity without clutter. Focus on "what" and "why", not "how".

### Mandatory Docstrings

- **Standard Docstrings**: Every exported function, component, or hook **MUST** have a **single-line docstring** using `/** ... */`.
- **Module-Level JSDoc**: EVERY single file MUST have a high-level description at the top following the standard template.

### Module-Level Template (at top of file)

```ts
/**
 * <Module Name> Module
 *
 * Responsibilities:
 * - <Primary task 1>
 * - <Primary task 2>
 *
 * Boundaries:
 * - <What this module does NOT handle>
 */
```

### Rules & Anti-Patterns

- **Prefer Single-Line**: Keep descriptions as concise as possible.
- Avoid repeating obvious information or duplicating TypeScript types in comments.
- **File-Level Mandatory**: Every single file MUST have the module-level docstring template at the top.

## 5. Strict Discipline & Anti-Patterns (FAANG-Level)

- **Stop Using `any`**: TypeScript safety is mandatory. No untyped code allowed.
- **No Inline Logic**: Do NOT put complex logic or mapping inside JSX. Extract into variables or hooks.
- **No Monoliths**: Break large components/hooks into smaller, focused modules immediately.
- **No Hardcoding**: Use constants or environment variables for all magic values and URLs.

---

# Universal Python Coding Standards

Follow these rules for all Python development to ensure production-grade quality, security, and maintainability.

## 1. Documentation & Readability

- **Mandatory Docstrings**: Every module, class, and public function/method must have a **single-line docstring** using `"""Triple double quotes"""`.
- **Formatting**: Add exactly one empty line immediately following any docstring. Keep lines under 100 characters.
- **Indentation**: Never put multiple statements on a single line (avoid `if x: return y`).

## 2. Imports & Best Practices

- **Ordering**: Organize imports: 1. Standard Library, 2. Third-party, 3. Local (separated by single newlines).
- **Security**: Specify `encoding="utf-8"` in all `open()` calls.
- **Closure Safety**: Fix "cell variable defined in loop" by passing loop variables as default arguments to lambdas/nested functions.
- **No Dead Code**: Remove unused variables, arguments, and imports immediately.

## 3. Performance & Logging

- **Structured Logging**: Use contextual metadata in all logs.
- **Asynchronous I/O**: Use `asyncio` for I/O bound tasks wherever applicable.

# Workspace Management (Commented Out)

# .vscode/ - Project-specific editor settings and debug configurations.

# .github/ - GitHub Actions workflows and project instructions.
