declare module 'react-native' {
  import * as React from 'react';

  export type Falsy = false | null | undefined;
  export type DimensionValue = number | string;
  export type StyleProp<T> = T | Falsy | ReadonlyArray<StyleProp<T>>;

  export interface ViewStyle {
    alignItems?: string;
    alignSelf?: string;
    backgroundColor?: string;
    borderBottomColor?: string;
    borderBottomWidth?: number;
    borderColor?: string;
    borderLeftColor?: string;
    borderLeftWidth?: number;
    borderRadius?: number;
    borderRightColor?: string;
    borderRightWidth?: number;
    borderTopColor?: string;
    borderTopWidth?: number;
    borderBottomLeftRadius?: number;
    borderBottomRightRadius?: number;
    bottom?: number;
    flex?: number;
    flexDirection?: 'row' | 'column';
    gap?: number;
    height?: DimensionValue;
    justifyContent?: string;
    margin?: number;
    marginBottom?: number;
    marginHorizontal?: number;
    marginLeft?: number;
    marginRight?: number;
    marginTop?: number;
    marginVertical?: number;
    maxHeight?: DimensionValue;
    maxWidth?: DimensionValue;
    minHeight?: DimensionValue;
    opacity?: number;
    padding?: number;
    paddingBottom?: number;
    paddingHorizontal?: number;
    paddingTop?: number;
    paddingVertical?: number;
    width?: DimensionValue;
  }

  export interface TextStyle extends ViewStyle {
    color?: string;
    fontSize?: number;
    fontWeight?: string;
    lineHeight?: number;
  }

  export interface PressableStateCallbackType {
    pressed: boolean;
  }

  export interface BaseProps {
    children?: React.ReactNode;
  }

  export interface ViewProps extends BaseProps {
    style?: StyleProp<ViewStyle>;
  }

  export interface TextProps extends BaseProps {
    numberOfLines?: number;
    style?: StyleProp<TextStyle>;
  }

  export interface ActivityIndicatorProps extends ViewProps {
    color?: string;
    size?: 'small' | 'large' | number;
  }

  export interface PressableProps extends ViewProps {
    accessibilityLabel?: string;
    accessibilityRole?: string;
    disabled?: boolean;
    onPress?: () => void;
    style?: StyleProp<ViewStyle> | ((state: PressableStateCallbackType) => StyleProp<ViewStyle>);
  }

  export interface TextInputProps extends BaseProps {
    accessibilityLabel?: string;
    editable?: boolean;
    maxLength?: number;
    multiline?: boolean;
    onChangeText?: (text: string) => void;
    placeholder?: string;
    placeholderTextColor?: string;
    returnKeyType?: string;
    style?: StyleProp<TextStyle | ViewStyle>;
    value?: string;
  }

  export interface KeyboardAvoidingViewProps extends ViewProps {
    behavior?: 'height' | 'position' | 'padding';
    keyboardVerticalOffset?: number;
  }

  export interface FlatListProps<ItemT> extends ViewProps {
    contentContainerStyle?: StyleProp<ViewStyle>;
    data: readonly ItemT[] | null | undefined;
    keyExtractor?: (item: ItemT, index: number) => string;
    ListEmptyComponent?: React.ReactElement | null;
    renderItem: (info: { item: ItemT; index: number }) => React.ReactElement | null;
  }

  export interface FlatListInstance<ItemT> {
    scrollToEnd: (options?: { animated?: boolean }) => void;
  }

  export const View: React.ComponentType<ViewProps>;
  export const Text: React.ComponentType<TextProps>;
  export const ActivityIndicator: React.ComponentType<ActivityIndicatorProps>;
  export const Pressable: React.ComponentType<PressableProps>;
  export const TextInput: React.ComponentType<TextInputProps>;
  export const KeyboardAvoidingView: React.ComponentType<KeyboardAvoidingViewProps>;
  export class FlatList<ItemT> extends React.Component<FlatListProps<ItemT>> {
    scrollToEnd(options?: { animated?: boolean }): void;
  }

  export const StyleSheet: {
    create<T extends Record<string, ViewStyle | TextStyle>>(styles: T): T;
    hairlineWidth: number;
  };

  export const Platform: {
    OS: 'ios' | 'android' | 'web';
  };

  export const AppRegistry: {
    registerComponent(appKey: string, getComponentFunc: () => React.ComponentType<any>): void;
  };

  export const StatusBar: React.ComponentType<{ barStyle?: string }>;
}
