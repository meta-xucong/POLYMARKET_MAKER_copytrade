    def _rebalance_burst_to_base_queue(self) -> None:
        """【已废弃】在新 Burst/Base 隔离逻辑下，不再将 pending burst token 移到 base。
        
        原因：新逻辑通过降级运行中的 burst token 来释放槽位，
        保持 pending burst token 在高优先级队列中等待。
        """
        # 【修复】不再执行回挪，保持 pending_burst_topics 不变
        # 让 _schedule_pending_topics 中的优先级逻辑决定启动顺序
        pass

    def _enqueue_burst_topic(self, topic_id: str, *, promote: bool = False) -> None:
