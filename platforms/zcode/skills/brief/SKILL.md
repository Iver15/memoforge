---
name: brief
description: Создать краткую записку для руководителя из завершённого меморандума.
---

# Memoforge zcode — brief

Прочитай [протокол](../../router.md). `mf task list`: выбери указанный task_id либо последнюю задачу current_phase=done. Если её нет, сообщи это. Выполняй `mf brief next --workdir W` и протокол brief report с run_id. Исходный меморандум только читается. Ответ на текущий gate передай через brief report --text. Не запускай обычный memo next.
