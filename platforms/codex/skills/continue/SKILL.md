---
name: continue
description: Продолжить задачу Memoforge или передать ответ на открытое согласование.
---

# Memoforge codex — continue

Прочитай [протокол](../../router.md). `mf task resolve [task_id]` возвращает work_dir. При errors или unsupported покажи объяснение и остановись. Выполни next; если получен ответ пользователя на открытый gate, передай его с текущими step/attempt/generation по протоколу. Иначе исполняй цикл. При platform_mismatch задача продолжается в исходном хосте.
