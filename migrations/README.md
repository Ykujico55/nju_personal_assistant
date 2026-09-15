# Database migrations

`0001_core.sql` defines only core-owned tables. Production task F01 must add a migration runner and record applied versions transactionally. Business extensions create and migrate only their own `ext_*` schema through `MigrationProvider`; they must not edit this file or core tables.

Before applying any later destructive migration, create and verify a recovery point. Never silently edit an already-applied migration—add a new numbered migration instead.

