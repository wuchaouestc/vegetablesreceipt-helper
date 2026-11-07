"""统一预算：请求数 / 总时限 / 费用上界 / 分类配额。

《方案》第五节要求：「一起开启时由统一预算约束，不能每条分支再各自重复重试」，
「超时重试也计入预算；总请求数、费用、总时限任一超限即停止追加并转人工核对」。

预算用**拒绝**而不是抛异常的方式表达：上层拿到 False 就停止追加，
把剩下的疑点原样留给人工，而不是让整批识别失败。
"""
from __future__ import annotations

import math
import time

# 各分支的默认配额（每张图片内）。
DEFAULT_QUOTAS = {'enhancement_variants': 1, 'review_regions': 2}


class UnifiedBudget:
    """一次识别任务共享的预算账本。"""

    def __init__(self, max_requests=30, max_seconds=600, max_cost=None,
                 clock=time.monotonic, quotas=None):
        if type(max_requests) is not int or max_requests < 0:
            raise ValueError('max_requests must be a nonnegative integer')
        if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) \
                or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError('max_seconds must be a positive number')
        if max_cost is not None and (isinstance(max_cost, bool) or not isinstance(max_cost, (int, float))
                                     or not math.isfinite(max_cost) or max_cost < 0):
            raise ValueError('max_cost must be a nonnegative number')
        self.max_requests = max_requests
        self.max_seconds = max_seconds
        self.max_cost = max_cost
        self.clock = clock
        self.started = clock()
        self.requests = 0
        self.reserved_cost = 0.0
        self.spent_cost = 0.0
        self.by_kind = {}
        self.quota_used = {}
        self.quotas = dict(DEFAULT_QUOTAS)
        if quotas:
            self.quotas.update(quotas)
        self.stopped_reason = ''

    # ---------------------------------------------------------------- 查询
    @property
    def remaining_seconds(self):
        return max(0.0, self.max_seconds - (self.clock() - self.started))

    @property
    def remaining_requests(self):
        return max(0, self.max_requests - self.requests)

    def exhausted(self):
        return bool(self.stopped_reason)

    # ---------------------------------------------------------------- 占用
    def can_reserve(self, kind='unknown', upper_cost=None, quota=None):
        if self.stopped_reason:
            return False
        if self.requests >= self.max_requests:
            self.stopped_reason = '请求数已达上限 %d' % self.max_requests
            return False
        if self.remaining_seconds <= 0:
            self.stopped_reason = '总时限已到 %.0f 秒' % self.max_seconds
            return False
        if quota:
            name, limit = quota
            if limit is None:
                limit = self.quotas.get(name)
            if limit is not None and self.quota_used.get(name, 0) >= limit:
                self.stopped_reason = '%s 已达配额 %s' % (name, limit)
                return False
        if self.max_cost is not None:
            if upper_cost is None:
                self.stopped_reason = '设了费用上限但没有单项费用上界，无法放行'
                return False
            if not math.isfinite(upper_cost) or upper_cost < 0:
                raise ValueError('upper_cost must be a nonnegative number')
            if self.reserved_cost + upper_cost > self.max_cost:
                self.stopped_reason = '费用预算已到上限 %.4f' % self.max_cost
                return False
        return True

    def reserve(self, kind='unknown', upper_cost=None, quota=None):
        """占用一次请求额度。放行返回 True，被预算拒绝返回 False（不抛异常）。"""
        if not self.can_reserve(kind, upper_cost, quota):
            return False
        self.requests += 1
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
        if quota:
            name = quota[0]
            self.quota_used[name] = self.quota_used.get(name, 0) + 1
        if self.max_cost is not None and upper_cost is not None:
            self.reserved_cost += upper_cost
        return True

    def settle(self, kind='unknown', actual_cost=0.0):
        """结算实际费用。实际费用低于预留时把差额退回预留额度。"""
        if not isinstance(actual_cost, (int, float)) or actual_cost < 0:
            return
        self.spent_cost += float(actual_cost)
        if self.max_cost is not None:
            self.reserved_cost = max(0.0, self.reserved_cost - float(actual_cost))

    def stop(self, reason):
        """主动停止追加（例如用户取消）。"""
        if not self.stopped_reason:
            self.stopped_reason = str(reason)

    # ---------------------------------------------------------------- 报告
    def usage(self):
        return {'requests': self.requests, 'max_requests': self.max_requests,
                'elapsed_seconds': round(self.clock() - self.started, 3),
                'max_seconds': self.max_seconds,
                'reserved_cost': round(self.reserved_cost, 6),
                'spent_cost': round(self.spent_cost, 6),
                'max_cost': self.max_cost,
                'by_kind': dict(self.by_kind),
                'quota_used': dict(self.quota_used),
                'quotas': dict(self.quotas),
                'stopped_reason': self.stopped_reason}

    def as_dict(self):
        return self.usage()
