# Synthetic Test Repository Architecture & Ground Truth Spec

This document details the architectural layout, entity relationship mapping, and testing ground truth for validating AST parsing, pgvector semantic indexing, and graph-based retrieval in the AI Codebase Intelligence Platform.

---

## 1. Domain & Layering Overview

The synthetic test repository models a simple multi-tenant user authentication and repository persistence domain implemented in both **Python** and **TypeScript**.

### Layers
- **Interfaces / Abstractions**: Pure abstract base classes (`Repository` in Python, `Repository<T>` in TS) defining persistence contracts.
- **Domain Models**: Data entities (`BaseModel`, `UserModel`, `AdminUser` in Python; `UserRecord`, `UserModel` in TS).
- **Services**: Concrete application services (`AuthService`, `UserService` in Python; `UserService` in TS) handling validation and business flows.
- **Utilities**: Standalone formatting helpers (`formatting.py`) decoupled from core business services.
- **Entrypoints**: Execution scripts (`main.py` in Python, `index.ts` in TS).

---

## 2. Relationship Ground Truth Matrix

The platform's Tree-sitter indexer and graph database MUST extract and link the following relationship types:

| Relationship Type | Source Entity | Target Entity | Location | Validation Notes |
| :--- | :--- | :--- | :--- | :--- |
| **INHERITS** | `UserModel` | `BaseModel` | `python/models/user.py` | Single inheritance |
| **INHERITS** | `AdminUser` | `UserModel` | `python/models/admin.py` | 3-level deep chain (`BaseModel` -> `UserModel` -> `AdminUser`) |
| **IMPLEMENTS** | `AuthService` | `Repository` | `python/services/auth_service.py` | Python ABC subclassing interface |
| **IMPLEMENTS** | `UserService` | `Repository<T>` | `typescript/services/user.service.ts` | TypeScript `implements` interface |
| **IMPORTS** | `user_service.py` | `auth_service.py` | `python/services/user_service.py` | Cross-module import |
| **IMPORTS** | `admin.py` | `user.py` | `python/models/admin.py` | Aliased relative import (`from .user import UserModel as BaseUserEntity`) |
| **IMPORTS** | `user.service.ts` | `repository.interface.ts` | `typescript/services/user.service.ts` | Aliased TS import (`import { Repository as IRepository }`) |
| **CALLS** (3-hop chain) | `main.py` -> `UserService.login_user` | `AuthService.authenticate_user` | Python entrypoint | Hop 1: `main` calls `login_user` |
| **CALLS** (3-hop chain) | `UserService.login_user` | `AuthService.authenticate_user` | `python/services/user_service.py` | Hop 2: `user_service` calls `auth_service` |
| **CALLS** (3-hop chain) | `AuthService.authenticate_user` | `UserModel.to_dict` / `BaseModel.to_dict` | `python/services/auth_service.py` | Hop 3: `auth_service` calls model base method |

---

## 3. Ground Truth Disambiguation & Test Cases

1. **Identically Named Methods Disambiguation**:
   - `UserModel.validate(self)` in `python/models/user.py`: Validates user entity fields (email format and entity ID).
   - `AuthService.validate(self, token: str)` in `python/services/auth_service.py`: Validates security token format (Bearer token string check).
   - *Test Goal*: AST indexer must correctly bind symbol calls to their respective class contexts rather than colliding on name `validate`.

2. **Semantic / Textual Similarity Disambiguation**:
   - `format_user_record()` in `python/utils/formatting.py`: Formats user model data dictionary.
   - `format_audit_log()` in `python/utils/formatting.py`: Formats system audit event payload.
   - *Test Goal*: Vector similarity alone will yield high cosine similarity (>0.90) between these functions due to identical code structure/variables (`data`, `prefix`, `lines`). Graph/context filtering must differentiate them based on caller scope and docstring semantics.

3. **Orphan File Handling**:
   - `python/utils/formatting.py` contains zero imports from repo files and zero outgoing calls to other repo files.
   - *Test Goal*: Vector retriever must find `formatting.py` for utility search queries while relationship parser registers 0 edges connected to other modules.
