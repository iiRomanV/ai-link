import psutil
import time
from typing import Dict, Any, List
from datetime import datetime

class ResourceManager:
    """Собирает статистику выполнения задач и предоставляет данные о загрузке системы."""
    def __init__(self):
        self.task_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.total_duration = 0.0  # суммарное время выполнения в секундах
        self.last_results: List[Dict[str, Any]] = []  # последние 10 результатов

    def get_system_usage(self) -> Dict[str, float]:
        """Возвращает текущую загрузку CPU и памяти в процентах."""
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory().percent
        return {"cpu": cpu, "memory": mem}

    def record_task(self, success: bool, duration: float, task: str = ""):
        """Записывает результат выполнения задачи."""
        self.task_count += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.total_duration += duration

        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "task": task[:50],  # ограничиваем длину для хранения
            "success": success,
            "duration": round(duration, 2)
        }
        self.last_results.insert(0, entry)
        # Храним только последние 10 записей
        if len(self.last_results) > 10:
            self.last_results.pop()

    def get_statistics(self) -> Dict[str, Any]:
        """Возвращает общую статистику."""
        avg_duration = self.total_duration / self.task_count if self.task_count > 0 else 0.0
        return {
            "task_count": self.task_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_duration": round(self.total_duration, 2),
            "average_duration": round(avg_duration, 2),
            "last_results": self.last_results
        }

# Глобальный экземпляр для использования в приложении
resource_manager = ResourceManager()
