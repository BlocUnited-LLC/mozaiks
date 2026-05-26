/**
 * @mozaiks/chat-ui/ui — cross-platform UI layer
 *
 * Components and screens built on React Native primitives.
 * On web: react-native-web translates them to DOM.
 * On native: react-native renders them natively.
 *
 * Import from here in both app/ (web) and clients/mobile/ (native).
 */

export { default as MessageBubble } from './components/MessageBubble';
export { default as MessageInput } from './components/MessageInput';
export { default as ConversationListScreen } from './screens/ConversationListScreen';
export { default as ChatScreen } from './screens/ChatScreen';
export { default as RootNavigator } from './navigation';

export {
  ActionButton,
  Alert,
  AlertBanner,
  AnalyticsSummaryStrip,
  Button,
  CodeBlock,
  CollectionToolbar,
  DataTable,
  emitAppEvent,
  Empty,
  ErrorState,
  ContentRail,
  FileList,
  Form,
  Grid,
  IconButton,
  InlineEmptyState,
  LinkButton,
  LoadingState,
  Metric,
  Modal,
  PageHeader,
  Panel,
  PerformanceTileGrid,
  ProgressTracker,
  ResourceList,
  ResourceTable,
  SegmentedBar,
  SegmentedControl,
  SlideOver,
  Skeleton,
  StatusPill,
  SummaryStrip,
  SurfaceCard,
  Timeline,
} from './primitives/index.js';
