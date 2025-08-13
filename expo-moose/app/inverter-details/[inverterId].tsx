import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  SafeAreaView,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  RefreshControl,
} from 'react-native';
import { useLocalSearchParams, useRouter, Stack } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@/hooks/useTheme';
import { getInverterProfile, getSystemStatusDetails, SystemStatusDetails } from '@/api/api';
import { Card, Chip, Divider } from 'react-native-paper';

const formatDateTime = (isoString: string | null | undefined): string => {
  if (!isoString) return "N/A";
  try {
    return new Date(isoString).toLocaleString();
  } catch (e) {
    return "Invalid Date";
  }
};

const formatDate = (isoString: string | null | undefined): string => {
  if (!isoString) return "N/A";
  try {
    return new Date(isoString).toLocaleDateString();
  } catch (e) {
    return "Invalid Date";
  }
};

export default function InverterDetailsScreen() {
  const { inverterId, inverterIds: inverterIdsParam, systemName, systemId } = useLocalSearchParams<{ 
    inverterId?: string;
    inverterIds?: string;
    systemName?: string;
    systemId?: string;
  }>();
  const router = useRouter();
  const { isDarkMode, colors } = useTheme();
  
  const [inverterIds, setInverterIds] = useState<string[]>([]);
  const [inverterProfiles, setInverterProfiles] = useState<Record<string, any>>({});
  const [statusDetails, setStatusDetails] = useState<SystemStatusDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    if (!inverterIdsParam || typeof inverterIdsParam !== "string" || !systemId) {
      setError("Invalid or missing inverter data.");
      setLoading(false);
      return;
    }

    const fetchInverterData = async () => {
      try {
        setLoading(true);
        setError(null);
        
        // Parse the inverter IDs from the parameter
        const parsedInverterIds = JSON.parse(inverterIdsParam);
        setInverterIds(parsedInverterIds);

        // Fetch both profiles and status details in parallel
        const [profilesResult, statusResult] = await Promise.allSettled([
          // Fetch profiles for each inverter
          Promise.all(parsedInverterIds.map(async (id: string) => {
            try {
              const profile = await getInverterProfile(id);
              return { id, profile };
            } catch (error) {
              console.error(`Failed to fetch profile for inverter ${id}:`, error);
              return { id, profile: null };
            }
          })),
          // Fetch system status details
          getSystemStatusDetails(systemId)
        ]);

        // Process profiles
        if (profilesResult.status === "fulfilled") {
          const profileMap: Record<string, any> = {};
          profilesResult.value.forEach((result) => {
            if (result.profile) {
              profileMap[result.id] = result.profile;
            }
          });
          setInverterProfiles(profileMap);
        }

        // Process status details
        if (statusResult.status === "fulfilled") {
          setStatusDetails(statusResult.value);
        }
        
      } catch (err) {
        console.error('Error fetching inverter data:', err);
        setError('Failed to load inverter data. Please try again.');
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    };

    fetchInverterData();
  }, [inverterIdsParam, systemId]);

  const onRefresh = () => {
    setRefreshing(true);
    // Re-trigger the effect by updating a state that will cause re-fetch
    setLoading(true);
  };

  // Helper function to get real-time status of an inverter
  const getInverterStatus = (inverterId: string) => {
    if (!statusDetails) return { status: 'unknown', color: '#9E9E9E', icon: 'help-circle' };
    
    if (statusDetails.GreenInverters?.includes(inverterId)) {
      return { status: 'online', color: '#4CAF50', icon: 'checkmark-circle' };
    }
    if (statusDetails.RedInverters?.includes(inverterId)) {
      return { status: 'error', color: '#F44336', icon: 'alert-circle' };
    }
    if (statusDetails.MoonInverters?.includes(inverterId)) {
      return { status: 'sleeping', color: '#9E9E9E', icon: 'moon' };
    }
    
    return { status: 'unknown', color: '#9E9E9E', icon: 'help-circle' };
  };

  // Summary Component - shows overview of inverters
  const InverterSummary = () => {
    const onlineInverters = inverterIds.filter(id => getInverterStatus(id).status === 'online');
    const errorInverters = inverterIds.filter(id => getInverterStatus(id).status === 'error');
    const sleepingInverters = inverterIds.filter(id => getInverterStatus(id).status === 'sleeping');

    return (
      <View style={[styles.statusCard, { backgroundColor: colors.card }]}>
        <View style={styles.statusHeader}>
          <Ionicons 
            name="hardware-chip" 
            size={32} 
            color={colors.primary} 
          />
          <View style={styles.statusInfo}>
            <Text style={[styles.statusTitle, { color: colors.text }]}>
              {systemName ? `${systemName} - Inverters` : 'Inverter Details'}
            </Text>
            <Text style={[styles.statusValue, { color: colors.primary }]}>
              {inverterIds.length} INVERTER{inverterIds.length !== 1 ? 'S' : ''}
            </Text>
          </View>
        </View>
        
        <View style={styles.statusStats}>
          <View style={styles.statItem}>
            <Text style={[styles.statValue, { color: colors.text }]}>
              {inverterIds.length}
            </Text>
            <Text style={[styles.statLabel, { color: colors.tabIconDefault }]}>
              Total
            </Text>
          </View>
          
          <View style={styles.statItem}>
            <Text style={[styles.statValue, { color: '#4CAF50' }]}>
              {onlineInverters.length}
            </Text>
            <Text style={[styles.statLabel, { color: colors.tabIconDefault }]}>
              Online
            </Text>
          </View>
          
          <View style={styles.statItem}>
            <Text style={[styles.statValue, { color: '#F44336' }]}>
              {errorInverters.length}
            </Text>
            <Text style={[styles.statLabel, { color: colors.tabIconDefault }]}>
              Error
            </Text>
          </View>
          
          <View style={styles.statItem}>
            <Text style={[styles.statValue, { color: '#9E9E9E' }]}>
              {sleepingInverters.length}
            </Text>
            <Text style={[styles.statLabel, { color: colors.tabIconDefault }]}>
              Sleeping
            </Text>
          </View>
        </View>
      </View>
    );
  };

  // Inverter Info Component - shows individual inverter cards
  const InverterInfo = ({ inverterId }: { inverterId: string }) => {
    const profile = inverterProfiles[inverterId];
    
    if (!profile) {
      return (
        <View style={[styles.section, { backgroundColor: colors.card }]}>
          <View style={styles.sectionHeader}>
            <Ionicons name="hardware-chip-outline" size={24} color={colors.tabIconDefault} />
            <Text style={[styles.sectionTitle, { color: colors.text }]}>
              Inverter {inverterId.substring(0, 8)}...
            </Text>
          </View>
          <Text style={[styles.emptyText, { color: colors.tabIconDefault }]}>
            Profile data not available
          </Text>
        </View>
      );
    }

    const statusInfo = getInverterStatus(inverterId);

    return (
      <View style={[styles.section, { backgroundColor: colors.card }]}>
        <View style={styles.sectionHeader}>
          <Ionicons 
            name={statusInfo.icon as any} 
            size={24} 
            color={statusInfo.color} 
          />
          <Text style={[styles.sectionTitle, { color: colors.text }]}>
            {profile.deviceName || `Inverter ${inverterId.substring(0, 8)}...`}
          </Text>
          <View style={styles.statusChips}>
            <View style={[
              styles.statusChip,
              { backgroundColor: statusInfo.color + '22' }
            ]}>
              <Text style={[
                styles.statusChipText,
                { color: statusInfo.color }
              ]}>
                {statusInfo.status.toUpperCase()}
              </Text>
            </View>
          </View>
        </View>

        {/* Basic Information */}
        <View style={styles.inverterInfoGrid}>
          <View style={styles.inverterInfoRow}>
            <Text style={[styles.inverterInfoLabel, { color: colors.tabIconDefault }]}>
              Type:
            </Text>
            <Text style={[styles.inverterInfoValue, { color: colors.text }]}>
              {profile.deviceTypeDetails || "Inverter"}
            </Text>
          </View>

          <View style={styles.inverterInfoRow}>
            <Text style={[styles.inverterInfoLabel, { color: colors.tabIconDefault }]}>
              Manufacturer:
            </Text>
            <Text style={[styles.inverterInfoValue, { color: colors.text }]}>
              {profile.deviceManufacturer || "N/A"}
            </Text>
          </View>

          <View style={styles.inverterInfoRow}>
            <Text style={[styles.inverterInfoLabel, { color: colors.tabIconDefault }]}>
              Serial Number:
            </Text>
            <Text style={[styles.inverterInfoValue, { color: colors.text }]}>
              {profile.serialNumber || "N/A"}
            </Text>
          </View>

          <View style={styles.inverterInfoRow}>
            <Text style={[styles.inverterInfoLabel, { color: colors.tabIconDefault }]}>
              AC Power:
            </Text>
            <Text style={[styles.inverterInfoValue, { color: colors.text }]}>
              {profile.nominalAcPower ? `${profile.nominalAcPower}W` : "N/A"}
            </Text>
          </View>

          <View style={styles.inverterInfoRow}>
            <Text style={[styles.inverterInfoLabel, { color: colors.tabIconDefault }]}>
              Installed:
            </Text>
            <Text style={[styles.inverterInfoValue, { color: colors.text }]}>
              {formatDate(profile.activationDate)}
            </Text>
          </View>

          <View style={styles.inverterInfoRow}>
            <Text style={[styles.inverterInfoLabel, { color: colors.tabIconDefault }]}>
              Last Updated:
            </Text>
            <Text style={[styles.inverterInfoValue, { color: colors.text }]}>
              {formatDateTime(profile.updatedAt)}
            </Text>
          </View>
        </View>

        {/* Firmware Information */}
        {profile.firmware && (
          <>
            <View style={styles.firmwareHeader}>
              <Ionicons name="code-working" size={20} color={colors.primary} />
              <Text style={[styles.firmwareTitle, { color: colors.text }]}>
                Firmware
              </Text>
            </View>
            <View style={styles.inverterInfoGrid}>
              <View style={styles.inverterInfoRow}>
                <Text style={[styles.inverterInfoLabel, { color: colors.tabIconDefault }]}>
                  Installed:
                </Text>
                <Text style={[styles.inverterInfoValue, { color: colors.text }]}>
                  {profile.firmware.installedVersion || "N/A"}
                </Text>
              </View>
              <View style={styles.inverterInfoRow}>
                <Text style={[styles.inverterInfoLabel, { color: colors.tabIconDefault }]}>
                  Available:
                </Text>
                <Text style={[styles.inverterInfoValue, { color: colors.text }]}>
                  {profile.firmware.availableVersion || "N/A"}
                </Text>
              </View>
            </View>
          </>
        )}

        {/* MPP Trackers */}
        {profile.mppTrackers && profile.mppTrackers.length > 0 && (
          <>
            <View style={styles.firmwareHeader}>
              <Ionicons name="flash" size={20} color={colors.primary} />
              <Text style={[styles.firmwareTitle, { color: colors.text }]}>
                MPP Trackers
              </Text>
            </View>
            <View style={styles.inverterInfoGrid}>
              {profile.mppTrackers.map((tracker: any, index: number) => (
                <View key={index} style={styles.inverterInfoRow}>
                  <Text style={[styles.inverterInfoLabel, { color: colors.tabIconDefault }]}>
                    {tracker.name}:
                  </Text>
                  <Text style={[styles.inverterInfoValue, { color: colors.text }]}>
                    {tracker.power ? `${tracker.power}W` : "0W"}
                  </Text>
                </View>
              ))}
            </View>
          </>
        )}
      </View>
    );
  };

  if (loading) {
    return (
      <SafeAreaView style={[styles.container, { backgroundColor: colors.background }]}>
        <Stack.Screen
          options={{
            headerShown: true,
            title: "Loading...",
            headerStyle: {
              backgroundColor: isDarkMode ? colors.background : "#f5f5f5",
            },
            contentStyle: { 
              paddingBottom: 0,
            },
            headerShadowVisible: true,
            headerTintColor: colors.text,
            headerLeft: () => (
              <TouchableOpacity
                onPress={() => router.back()}
                style={{ marginLeft: 16, padding: 4, marginRight: 16 }}
              >
                <Ionicons name="arrow-back" size={24} color={colors.text} />
              </TouchableOpacity>
            ),
          }}
        />
        
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={[styles.loadingText, { color: colors.tabIconDefault }]}>
            Loading inverter data...
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={[styles.container, { backgroundColor: colors.background }]}>
        <Stack.Screen
          options={{
            headerShown: true,
            title: "Error",
            headerStyle: {
              backgroundColor: isDarkMode ? colors.background : "#f5f5f5",
            },
            contentStyle: { 
              paddingBottom: 0,
            },
            headerShadowVisible: true,
            headerTintColor: colors.text,
            headerLeft: () => (
              <TouchableOpacity
                onPress={() => router.back()}
                style={{ marginLeft: 16, padding: 4, marginRight: 16 }}
              >
                <Ionicons name="arrow-back" size={24} color={colors.text} />
              </TouchableOpacity>
            ),
          }}
        />
        
        <View style={styles.errorContainer}>
          <Ionicons name="alert-circle" size={64} color={colors.statusError} />
          <Text style={[styles.errorTitle, { color: colors.text }]}>
            Error Loading Inverters
          </Text>
          <Text style={[styles.errorMessage, { color: colors.tabIconDefault }]}>
            {error || "Invalid or missing System ID. Try Again."}
          </Text>
          <TouchableOpacity
            style={[styles.retryButton, { backgroundColor: colors.primary }]}
            onPress={onRefresh}
          >
            <Text style={styles.retryButtonText}>Try Again</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          headerShown: true,
          title: systemName 
            ? `${systemName} - Inverters` 
            : inverterIds.length > 1 
              ? "System Inverters" 
              : "Inverter Details",
          headerStyle: {
            backgroundColor: isDarkMode ? colors.background : "#f5f5f5",
          },
          contentStyle: { 
            paddingBottom: 0,
          },
          headerShadowVisible: true,
          headerTintColor: colors.text,
          headerLeft: () => (
            <TouchableOpacity
              onPress={() => router.back()}
              style={{ marginLeft: 16, padding: 4, marginRight: 16 }}
            >
              <Ionicons name="arrow-back" size={24} color={colors.text} />
            </TouchableOpacity>
          ),
        }}
      />

      <ScrollView 
        style={styles.content} 
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
        }
      >
        {/* Summary Component - only show when viewing multiple inverters */}
        {inverterIds.length > 1 && <InverterSummary />}

        {/* Inverter Information Components */}
        {inverterIds.map((inverterId) => (
          <InverterInfo key={inverterId} inverterId={inverterId} />
        ))}

        {inverterIds.length === 0 && (
          <View style={[styles.section, { backgroundColor: colors.card }]}>
            <View style={styles.sectionHeader}>
              <Ionicons name="hardware-chip-outline" size={24} color={colors.tabIconDefault} />
              <Text style={[styles.sectionTitle, { color: colors.text }]}>
                No Inverters Found
              </Text>
            </View>
            <Text style={[styles.emptyText, { color: colors.tabIconDefault }]}>
              No inverters found for this system.
            </Text>
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    flex: 1,
    padding: 16,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  loadingText: {
    marginTop: 16,
    fontSize: 16,
  },
  errorContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 32,
  },
  errorTitle: {
    fontSize: 24,
    fontWeight: '600',
    marginTop: 16,
    marginBottom: 8,
  },
  errorMessage: {
    fontSize: 16,
    textAlign: 'center',
    marginBottom: 24,
  },
  retryButton: {
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
  },
  retryButtonText: {
    color: 'white',
    fontSize: 16,
    fontWeight: '600',
  },
  statusCard: {
    borderRadius: 12,
    padding: 20,
    marginBottom: 20,
    elevation: 2,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
  },
  statusHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 20,
  },
  statusInfo: {
    marginLeft: 16,
    flex: 1,
  },
  statusTitle: {
    fontSize: 16,
    marginBottom: 4,
  },
  statusValue: {
    fontSize: 24,
    fontWeight: '700',
  },
  statusStats: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginBottom: 16,
  },
  statItem: {
    alignItems: 'center',
  },
  statValue: {
    fontSize: 24,
    fontWeight: '700',
    marginBottom: 4,
  },
  statLabel: {
    fontSize: 12,
    textAlign: 'center',
  },
  section: {
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
    elevation: 2,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '600',
    marginLeft: 12,
    flex: 1,
  },
  statusChips: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  statusChip: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  statusChipText: {
    fontSize: 12,
    fontWeight: '600',
  },
  inverterInfoGrid: {
    gap: 8,
    marginBottom: 16,
  },
  inverterInfoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 4,
  },
  inverterInfoLabel: {
    fontSize: 14,
    fontWeight: '500',
    flex: 1,
  },
  inverterInfoValue: {
    fontSize: 14,
    textAlign: 'right',
    flex: 1,
  },
  firmwareHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
    marginTop: 8,
  },
  firmwareTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginLeft: 8,
  },
  emptyText: {
    fontSize: 14,
    fontStyle: 'italic',
    textAlign: 'center',
    paddingVertical: 16,
  },
}); 