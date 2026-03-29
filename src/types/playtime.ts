/** Types for the Unifideck activity tracking & play time system. */

export interface PlaySession {
  id: number;
  game_id: number;
  started_at: string;
  ended_at: string | null;
  duration_secs: number | null;
  end_reason: string;
  title: string;
  store: string;
  steam_app_id: number | null;
  proton_tool: string | null;
  is_manual: number;
  session_note: string | null;
}

export interface GameStats {
  game_id: number;
  title: string;
  store: string;
  steam_app_id: number | null;
  total_secs: number;
  total_sessions: number;
  avg_session_secs: number;
  min_session_secs: number | null;
  max_session_secs: number;
  first_played_at: string | null;
  last_played_at: string | null;
  current_streak_days: number;
  longest_streak_days: number;
}

export interface DailyTotal {
  date: string;
  total_secs: number;
  session_count: number;
  games_played: number;
}

export interface StoreSummary {
  store: string;
  total_secs: number;
  game_count: number;
  session_count: number;
  most_played_title: string | null;
  most_played_secs: number;
}

export interface OverallStats {
  total_secs: number;
  total_sessions: number;
  total_games_played: number;
  most_active_hour: number | null;
  most_active_day: string | null;
  average_daily_secs: number;
  this_week_secs: number;
  last_week_secs: number;
}

export interface HourDistribution {
  hour: number;
  session_count: number;
  total_secs: number;
}

export interface WeeklyComparison {
  this_week: {
    total_secs: number;
    session_count: number;
    games_played: number;
  };
  last_week: {
    total_secs: number;
    session_count: number;
    games_played: number;
  };
  change_percent: number;
}
