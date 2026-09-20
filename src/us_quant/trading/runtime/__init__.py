"""The trading runtime: one package, one responsibility per module.

Runtime v2A split the mixed ``AutoQuantEngine`` into two runtimes and the
modules that support them:

``models.py``
    what the strategy may read -- candidates, frozen position and policy views,
    and the shape of one scan's answer.
``artifacts.py``
    the session's own artifacts, including the snapshot the window renders.
    Separate because a snapshot carries order identity and the strategy must not
    be able to reach that, even by importing a module that mentions it.
``signals.py``
    signal detection: minute histories, the entry gates and the ranking.
``strategy.py``
    ``StrategyRuntime`` -- what the strategy *wants*, as ``TradeProposal``s.
``session.py``
    ``SessionState`` -- the session's own state and its transitions.
``portfolio.py``
    ``SessionBook`` -- positions, cash, PnL, pending orders and fills, and the
    projection of them into the risk domain.
``dispatch.py``
    ``OrderDispatch`` -- the one place risk and execution are called.
``trading.py``
    ``TradingRuntime`` -- the trading session that turns proposals into orders.

The chain is ``Market Data → StrategyRuntime → (proposals) → TradingRuntime →
RiskApplication → ExecutionApplication``, and it is one-way: the strategy never
learns that a verdict exists, and the dispatch never learns why a proposal was
made.  Nothing in this package imports a broker, a store or a widget -- the
composition root does that.

Each module is kept under 500 lines by an architecture guard, because the risk
of this shape is a "split" that is really one large class renamed.
"""