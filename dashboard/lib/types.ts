export interface Signal {
  id: number;
  coin_symbol: string;
  coin_name: string;
  total_score: number;
  technical_score: number;
  news_score: number;
  gemini_explanation: string;
  fired_at: string;
  strategy: string;
}

export interface Position {
  id: number;
  signal_id: number;
  coin_symbol: string;
  entry_price: number;
  entry_at: string;
  exit_price: number | null;
  exit_at: string | null;
  outcome: string | null;
  pnl_pct: number | null;
  strategy: string;
}

export interface StrategyStats {
  total_closed: number;
  wins: number;
  losses: number;
  win_rate: number;
  open_positions: number;
  signals_today: number;
  avg_pnl_pct: number;
}

export interface Stats {
  overall: StrategyStats;
  standard: StrategyStats;
  whale: StrategyStats;
}

export interface BotConfig {
  signal_threshold: number;
  take_profit_pct: number;
  stop_loss_pct: number;
  max_hold_hours: number;
  scan_interval_minutes: number;
  tracking_interval_seconds: number;
  whale_enabled: boolean;
  whale_take_profit_pct: number;
  whale_stop_loss_pct: number;
  whale_max_hold_hours: number;
}

export type LiveUpdate = Record<number, { current_price: number; pnl_pct: number }>;
