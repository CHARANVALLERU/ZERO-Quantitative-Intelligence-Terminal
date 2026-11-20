"""
Background tasks manager for ZERO Terminal
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, Callable, Optional

logger = logging.getLogger('ZERO_Tasks')

# Background task registry
background_tasks: Dict[str, Callable] = {}


def register_task(task_name: str):
    """Decorator to register a background task."""
    def decorator(func):
        background_tasks[task_name] = func
        logger.info(f"Registered background task: {task_name}")
        return func
    return decorator


async def run_background_tasks():
    """Run all registered background tasks."""
    while True:
        try:
            logger.info(f"Running background tasks: {len(background_tasks)} tasks registered")
            
            # Execute all registered tasks
            for task_name, task_func in background_tasks.items():
                try:
                    logger.info(f"Executing task: {task_name}")
                    result = await task_func()
                    logger.info(f"Task completed: {task_name} - {result}")
                except Exception as e:
                    logger.error(f"Task failed: {task_name} - {e}")
            
            # Wait for next cycle (default 60 seconds)
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            logger.info("Background tasks cancelled")
            break
        except Exception as e:
            logger.error(f"Background tasks error: {e}")
            await asyncio.sleep(10)  # Wait before retry


# Individual task implementations
def periodic_market_update():
    """Periodic market data update task."""
    return {"status": "market_data_updated", "timestamp": datetime.utcnow().isoformat()}


@register_task("market_update")
async def market_update_task():
    """Background task to update market data."""
    logger.info("Market update task started")
    
    # Update predictions
    try:
        from engine.prediction_matrix import generate_prediction_matrix
        matrix = generate_prediction_matrix()
        logger.info(f"Predictions updated: {len(matrix)} indices")
    except Exception as e:
        logger.error(f"Failed to update predictions: {e}")
    
    # Update live prices
    try:
        prices = {}
        for index in ["NIFTY 50", "BANKNIFTY", "SENSEX"]:
            try:
                from data.live_index_service import get_live_index_quote
                quote = get_live_index_quote(index)
                prices[index] = quote
            except Exception as e:
                logger.warning(f"Failed to get price for {index}: {e}")
        
        if prices:
            logger.info(f"Live prices updated: {len(prices)} indices")
    except Exception as e:
        logger.error(f"Failed to update live prices: {e}")
    
    # Update news
    try:
        from data.market_news import get_global_news
        from data.news_monitor import check_breaking
        
        news = get_global_news() or []
        breaking = check_breaking()
        
        logger.info(f"News updated: {len(news)} items, {len(breaking.get('breaking', []))} breaking")
    except Exception as e:
        logger.error(f"Failed to update news: {e}")
    
    return {"status": "completed"}


@register_task("strategy_evaluation")
async def strategy_evaluation_task():
    """Background task to evaluate trading strategies."""
    logger.info("Strategy evaluation task started")
    
    # Placeholder for strategy evaluation logic
    return {
        "status": "strategies_evaluated",
        "count": 5,
        "timestamp": datetime.utcnow().isoformat()
    }


@register_task("risk_monitoring")
async def risk_monitoring_task():
    """Background task to monitor risk metrics."""
    logger.info("Risk monitoring task started")
    
    # Placeholder for risk monitoring logic
    return {
        "status": "risk_monitored",
        "timestamp": datetime.utcnow().isoformat()
    }


# Task queue implementation
class TaskQueue:
    """Simple in-memory task queue."""
    
    def __init__(self):
        self.queue = asyncio.Queue()
    
    async def add_task(self, task_name: str, task_data: Optional[Dict] = None):
        """Add a task to the queue."""
        task = {
            "name": task_name,
            "data": task_data or {},
            "timestamp": datetime.utcnow().isoformat()
        }
        await self.queue.put(task)
        logger.info(f"Task queued: {task_name}")
    
    async def get_task(self):
        """Get next task from queue."""
        return await self.queue.get()
    
    async def process_tasks(self):
        """Process all tasks in queue."""
        while True:
            task = await self.get_task()
            try:
                task_func = background_tasks.get(task["name"])
                if task_func:
                    logger.info(f"Processing task: {task['name']}")
                    await task_func()
                else:
                    logger.warning(f"Unknown task: {task['name']}")
            except Exception as e:
                logger.error(f"Task failed: {task['name']} - {e}")


# Global task queue instance
queue = TaskQueue()
