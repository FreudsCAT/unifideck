/**
 * Type definitions for Steam Client APIs
 * These are based on observed behavior and may change with Steam updates
 */

export interface SteamApp {
  appid: number;
  display_name: string;
  sort_as: string;
  installed: boolean;
  is_shortcuts_app: boolean; // True for non-Steam games
  BIsShortcut(): boolean;
  size_on_disk: string;
  minutes_playtime_forever: string;
  minutes_playtime_last_two_weeks: string;
  rt_last_time_played: number;
  store_category: number[];
  app_type: number;
  canonicalAppType: number;
  local_per_client_data?: {
    is_hidden?: boolean;
  };
}

export interface SteamAppOverview extends SteamApp {
  icon_hash: string;
  review_score: number;
  review_percentage: number;
  GameID(): string;
  GetCapsuleImageURL(): string;
  GetHeaderImageURL(): string;
  GetLibraryImageURL(): string;
}

export interface SteamCollection {
  id: string;
  name: string;
  added_timestamp: number;
  bIsDynamic: boolean;
  visibleApps: number[];
}

export interface Unregisterable {
  unregister(): void;
}

// Global Steam Client API interfaces
declare global {
  interface Window {
    SteamClient?: {
      Apps?: {
        GetOwnedApps(): SteamApp[];
        GetNonSteamApps(): SteamApp[];
        GetAppOverview(appId: number): SteamAppOverview | null;
        RegisterForGameActionStart(
          callback: (
            gameActionId: number,
            appId: string,
            action: string,
            launchSource: number,
          ) => void,
        ): Unregisterable;
        CancelGameAction(gameActionId: number): void;
        RunGame(
          appId: string,
          launchOptions: string,
          param3: number,
          param4: number,
        ): void;
        TerminateApp(appId: string, force: boolean): void;
        ShowControllerConfigurator(appId: number): void;
        OpenAppSettingsDialog(appId: number, section: string): void;
        AddShortcut(
          appName: string,
          exePath: string,
          startDir: string,
          launchOptions: string,
        ): Promise<number>;
        RemoveShortcut(appId: number): void;
        SetShortcutName?(appId: number, name: string): void;
        SpecifyCompatTool(appId: number, strToolName: string): void;
        SetShortcutLaunchOptions(appId: number, options: string): void;
        GetPlaytime(
          appId: number,
        ): Promise<{ nPlaytimeForever: number; rtLastTimePlayed: number }>;
        SetThirdPartyControllerConfiguration?(
          appId: number,
          value: number,
        ): void;
      };
      Input?: {
        QueryControllerConfigsForApp(
          appId: number,
          controllerIndex: number,
          compatibleOnly: boolean,
        ): void;
        StartEditingControllerConfigurationForAppIDAndControllerIndex?(
          appId: number,
          controllerIndex: number,
        ): Promise<void> | void;
        SetSelectedConfigForApp(
          appId: number,
          controllerIndex: number,
          url: string,
          preview: boolean,
          applyToSameControllerType?: boolean,
        ): void;
        SaveEditingControllerConfiguration?(
          controllerIndex: number,
          sharedConfig: boolean,
        ): void;
        StopEditingControllerConfiguration?(controllerIndex: number): void;
        ClearSelectedConfigForApp?(
          appId: number,
          controllerIndex: number,
        ): void;
        RegisterForControllerConfigInfoMessages(
          callback: (msgs: ControllerConfigInfoMessage[]) => void,
        ): Unregisterable;
        RegisterForControllerListChanges(
          callback: (controllers: ControllerInfo[]) => void,
        ): Unregisterable;
      };
      GameSessions?: {
        RegisterForAppLifetimeNotifications(
          callback: (notification: {
            unAppID: number;
            bRunning: boolean;
            nInstanceID: number;
          }) => void,
        ): Unregisterable;
      };
      library?: {
        GetCollections(): SteamCollection[];
      };
    };
    collectionStore?: {
      userCollections?: Map<string, SteamCollection>;
      deckDesktopApps?: SteamCollection;
      localGamesCollection?: SteamCollection;
    };
    controllerConfiguratorStore?: {
      m_controllerList?: ControllerInfo[];
    };
    // Note: appStore is defined by @decky/ui globals
  }
}

export type StoreType =
  | "steam"
  | "epic"
  | "gog"
  | "amazon"
  | "microsoft"
  | "unknown";

export interface UnifideckGame {
  appId: number;
  title: string;
  store: StoreType;
  isInstalled: boolean;
  isShortcut: boolean;
  tags?: string[];
  deckVerified?: "verified" | "playable" | "unsupported" | "unknown";
  lastPlayed?: number;
  playtimeMinutes?: number;
}

// Controller configuration types (from SteamClient.Input)
export interface ControllerConfigInfoMessageList {
  appID: number;
  nControllerType: number;
  Title: string;
  URL: string;
  eExportType: number; // 0=Unknown, 3=Community, 4=Template, 5=Official, 6=OfficialDefault
  bUsesGamepad: boolean;
  bSelected: boolean;
  bOfficial: boolean;
  bUsesMouse: boolean;
  bUsesKeyboard: boolean;
  publishedFileID: string;
}

export interface ControllerConfigInfoMessageDone {
  appID: number;
  bGameQueryDone?: boolean;
  bPersonalQueryDone?: boolean;
  bCloudQueryDone?: boolean;
}

export type ControllerConfigInfoMessage =
  | ControllerConfigInfoMessageList
  | ControllerConfigInfoMessageDone;

export interface ControllerInfo {
  strName: string;
  eControllerType: number;
  nControllerIndex: number;
  bWireless: boolean;
}
