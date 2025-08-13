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
} from 'react-native';
import { useLocalSearchParams, useRouter, Stack } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@/hooks/useTheme';
import { getSystemStatusDetails, getInverterProfile, getSystemProfile, SystemStatusDetails } from '@/api/api';

export default function StatusDetailScreen() {
  const { systemId } = useLocalSearchParams<{ systemId?: string }>();
  const router = useRouter();
  const { isDarkMode, colors } = useTheme();
  
  const [statusDetails, setStatusDetails] = useState<SystemStatusDetails | null>(null);
  const [systemProfile, setSystemProfile] = useState<any>(null);
  const [inverterNames, setInverterNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!systemId || typeof systemId !== "string") {
      setError("Invalid or missing System ID.");
      setLoading(false);
      return;
    }

    const fetchStatusDetails = async () => {
      try {
        setLoading(true);
        setError(null);
        
        // Fetch both status details and system profile
        const [statusData, profileData] = await Promise.all([
          getSystemStatusDetails(systemId),
          getSystemProfile(systemId)
        ]);
        
        setStatusDetails(statusData);
        setSystemProfile(profileData);
        
        // Collect all unique inverter IDs
        const allInverterIds = [
          ...(statusData.GreenInverters || []),
          ...(statusData.RedInverters || []),
          ...(statusData.MoonInverters || [])
        ];
        
        // Fetch inverter profiles from your database
        if (allInverterIds.length > 0) {
          const namesMap: Record<string, string> = {};
          
          // Fetch each inverter profile individually
          await Promise.all(
            allInverterIds.map(async (inverterId) => {
              try {
                const profile = await getInverterProfile(inverterId);
                // Extract device name from the full profile data
                namesMap[inverterId] = profile.deviceName || inverterId;
              } catch (error) {
                console.error(`Failed to fetch profile for inverter ${inverterId}:`, error);
                // Fallback to ID if profile fetch fails
                namesMap[inverterId] = inverterId;
              }
            })
          );
          
          setInverterNames(namesMap);
        }
        
      } catch (err) {
        console.error('Error fetching status details:', err);
        setError('Failed to load status details. Please try again.');
      } finally {
        setLoading(false);
      }
    };

    fetchStatusDetails();
  }, [systemId]);

  const getStatusColor = (status: string) => {
    switch (status.toLowerCase()) {
      case 'green':
      case 'online':
        return '#4CAF50';
      case 'red':
      case 'error':
        return '#F44336';
      case 'moon':
      case 'sleeping':
        return '#9E9E9E';
      default:
        return '#9E9E9E';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status.toLowerCase()) {
      case 'green':
      case 'online':
        return 'checkmark-circle';
      case 'red':
      case 'error':
        return 'alert-circle';
      case 'moon':
      case 'sleeping':
        return 'moon';
      default:
        return 'help-circle';
    }
  };

  const formatLastUpdated = (dateString: string) => {
    try {
      // Ensure the date string is treated as UTC by adding 'Z' if no timezone indicator exists
      let utcDateString = dateString;
      if (!dateString.includes('Z') && !dateString.includes('+') && !dateString.includes('-', 10)) {
        utcDateString = dateString + 'Z';
      }
      
      // Parse the UTC date string
      const utcDate = new Date(utcDateString);
      
      // Get timezone from system profile, default to America/New_York if not available
      const timezone = systemProfile?.timezone || 'America/New_York';
      
      // Convert to the appropriate timezone
      const localizedDate = new Intl.DateTimeFormat('en-US', {
        timeZone: timezone,
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true
      }).format(utcDate);
      
      console.log('OLD DATE (UTC):', dateString)
      console.log('NEW DATE (' + timezone + '):', localizedDate)
      return localizedDate;
    } catch (error) {
      console.error('Error formatting date:', error);
      return dateString;
    }
  };

  const renderInverterSection = (title: string, inverters: string[], color: string, icon: string) => (
    <View style={[styles.section, { backgroundColor: colors.card }]}>
      <View style={styles.sectionHeader}>
        <Ionicons name={icon as any} size={24} color={color} />
        <Text style={[styles.sectionTitle, { color: colors.text }]}>
          {title} ({inverters.length})
        </Text>
      </View>
      
      {inverters.length > 0 ? (
        <View style={styles.inverterList}>
          {inverters.map((inverterId, index) => (
            <View key={index} style={[styles.inverterItem, { backgroundColor: colors.background }]}>
              <Text style={[styles.inverterText, { color: colors.text }]} numberOfLines={1}>
                {inverterNames[inverterId] || inverterId}
              </Text>
            </View>
          ))}
        </View>
      ) : (
        <Text style={[styles.emptyText, { color: colors.tabIconDefault }]}>
          No {title.toLowerCase()} found
        </Text>
      )}
    </View>
  );

  if (loading) {
    return (
      <SafeAreaView style={[styles.container, { backgroundColor: colors.background }]}>
        <Stack.Screen
          options={{
            headerShown: true,
            title: "System Status",
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
            Loading status details...
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
            title: "System Status",
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
            Error Loading Status
          </Text>
          <Text style={[styles.errorMessage, { color: colors.tabIconDefault }]}>
            {error}
          </Text>
          <TouchableOpacity
            style={[styles.retryButton, { backgroundColor: colors.primary }]}
            onPress={() => {
              setError(null);
              setLoading(true);
              // Re-trigger the effect by forcing a re-render
              window.location.reload?.();
            }}
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
          title: "System Status Details",
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

      <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
        {/* Overall Status Card */}
        <View style={[styles.statusCard, { backgroundColor: colors.card }]}>
          <View style={styles.statusHeader}>
            <Ionicons 
              name={getStatusIcon(statusDetails?.status || '')} 
              size={32} 
              color={getStatusColor(statusDetails?.status || '')} 
            />
            <View style={styles.statusInfo}>
              <Text style={[styles.statusTitle, { color: colors.text }]}>
                System Status
              </Text>
              <Text style={[styles.statusValue, { color: getStatusColor(statusDetails?.status || '') }]}>
                {statusDetails?.status?.toUpperCase() || 'UNKNOWN'}
              </Text>
            </View>
          </View>
          
          <View style={styles.statusStats}>
            <View style={styles.statItem}>
              <Text style={[styles.statValue, { color: colors.text }]}>
                {statusDetails?.TotalInverters || 0}
              </Text>
                             <Text style={[styles.statLabel, { color: colors.tabIconDefault }]}>
                 Total Inverters
               </Text>
             </View>
             
             <View style={styles.statItem}>
               <Text style={[styles.statValue, { color: '#4CAF50' }]}>
                 {statusDetails?.GreenInverters?.length || 0}
               </Text>
               <Text style={[styles.statLabel, { color: colors.tabIconDefault }]}>
                 Online
               </Text>
             </View>
             
             <View style={styles.statItem}>
               <Text style={[styles.statValue, { color: '#F44336' }]}>
                 {statusDetails?.RedInverters?.length || 0}
               </Text>
               <Text style={[styles.statLabel, { color: colors.tabIconDefault }]}>
                 Error
               </Text>
             </View>
             
             <View style={styles.statItem}>
               <Text style={[styles.statValue, { color: '#9E9E9E' }]}>
                 {statusDetails?.MoonInverters?.length || 0}
               </Text>
               <Text style={[styles.statLabel, { color: colors.tabIconDefault }]}>
                 Sleeping
               </Text>
            </View>
          </View>
          
                     {statusDetails?.lastUpdated && (
             <Text style={[styles.lastUpdated, { color: colors.tabIconDefault }]}>
               Last updated: {formatLastUpdated(statusDetails.lastUpdated)}
             </Text>
           )}
        </View>

        {/* Inverter Status Sections */}
        {renderInverterSection(
          'Online Inverters',
          statusDetails?.GreenInverters || [],
          '#4CAF50',
          'checkmark-circle'
        )}
        
        {renderInverterSection(
          'Error Inverters',
          statusDetails?.RedInverters || [],
          '#F44336',
          'alert-circle'
        )}
        
        {renderInverterSection(
          'Sleeping Inverters',
          statusDetails?.MoonInverters || [],
          '#9E9E9E',
          'moon'
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0,0,0,0.1)',
  },
  backButton: {
    padding: 8,
    marginRight: 8,
  },
  headerTitle: {
    fontSize: 20,
    fontWeight: '600',
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
  lastUpdated: {
    fontSize: 12,
    textAlign: 'center',
    fontStyle: 'italic',
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
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '600',
    marginLeft: 12,
  },
  inverterList: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  inverterItem: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    marginBottom: 4,
    minWidth: '45%',
    maxWidth: '48%',
  },
  inverterText: {
    fontSize: 12,
    fontFamily: 'monospace',
  },
  emptyText: {
    fontSize: 14,
    fontStyle: 'italic',
    textAlign: 'center',
    paddingVertical: 16,
  },
}); 