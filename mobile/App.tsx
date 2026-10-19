import React, { useEffect, useState } from "react";
import { ActivityIndicator } from "react-native";
import { StatusBar } from "expo-status-bar";
import { NavigationContainer } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { loadToken, setToken } from "./src/api";
import AuthScreen from "./src/screens/AuthScreen";
import BookScreen from "./src/screens/BookScreen";
import ChatScreen from "./src/screens/ChatScreen";
import PlanScreen from "./src/screens/PlanScreen";
import ResultsScreen from "./src/screens/ResultsScreen";
import SearchScreen, { Stack } from "./src/screens/SearchScreen";
import TripsScreen from "./src/screens/TripsScreen";
import { color } from "./src/ui";

const Tab = createBottomTabNavigator();
const Root = createNativeStackNavigator<Stack>();

function Tabs({ onLogout }: { onLogout: () => void }) {
  return (
    <Tab.Navigator screenOptions={{ tabBarActiveTintColor: color.brand, headerShown: false }}>
      <Tab.Screen name="Search" component={SearchScreen as never} />
      <Tab.Screen name="Plan" component={PlanScreen} />
      <Tab.Screen name="Assistant" component={ChatScreen} />
      <Tab.Screen name="Trips">{() => <TripsScreen onLogout={onLogout} />}</Tab.Screen>
    </Tab.Navigator>
  );
}

export default function App() {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    loadToken().then((t) => {
      setAuthed(!!t);
      setReady(true);
    });
  }, []);

  if (!ready) return <ActivityIndicator style={{ flex: 1 }} />;
  if (!authed) return <AuthScreen onDone={() => setAuthed(true)} />;

  return (
    <NavigationContainer>
      <StatusBar style="dark" />
      <Root.Navigator>
        <Root.Screen name="Tabs" options={{ headerShown: false }}>
          {() => <Tabs onLogout={() => setToken(null).then(() => setAuthed(false))} />}
        </Root.Screen>
        <Root.Screen name="Results" component={ResultsScreen} options={{ title: "Results" }} />
        <Root.Screen name="Book" component={BookScreen} options={{ title: "Confirm booking" }} />
      </Root.Navigator>
    </NavigationContainer>
  );
}
