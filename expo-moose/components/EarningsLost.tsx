import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@/hooks/useTheme';
import { useSession } from '@/utils/sessionContext';
import { getSystemStatusDetails, getInverterDailyData, getInverterStatus, getConsolidatedDailyData, InverterDailyData } from '@/api/api';

interface EarningsLostProps {
  systemId: string;
}

const EarningsLost: React.FC<EarningsLostProps> = ({ systemId }) => {
  const { isDarkMode, colors } = useTheme();
  const { systemStatuses } = useSession();
  const [totalEarningsLost, setTotalEarningsLost] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [earliestDownTime, setEarliestDownTime] = useState<Date | null>(null);

  console.log(`[EarningsLost] Component initialized for systemId: ${systemId}`);

  // Helper function to format date nicely
  const formatSinceDate = (date: Date): string => {
    try {
      return new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true
      }).format(date);
    } catch (error) {
      console.error('[EarningsLost] Error formatting date:', error);
      return date.toLocaleDateString();
    }
  };

  // Only show if system status is "error" (red)
  const systemStatus = systemStatuses[systemId];
  console.log(`[EarningsLost] System status for ${systemId}: ${systemStatus}`);
  
  if (systemStatus !== 'error') {
    console.log(`[EarningsLost] System ${systemId} is not in error state, hiding component`);
    return null;
  }

  // Helper function to parse sunrise/sunset time string to Date object in EST
  const parseTimeToEST = (timeStr: string, date: Date): Date => {
    try {
      // Parse time string like "05:29 AM" or "08:45 PM"
      const [time, period] = timeStr.split(' ');
      const [hours, minutes] = time.split(':').map(Number);
      
      // Convert to 24-hour format
      let hour24 = hours;
      if (period === 'PM' && hours !== 12) {
        hour24 += 12;
      } else if (period === 'AM' && hours === 12) {
        hour24 = 0;
      }
      
      // Create date object in EST
      const estDate = new Date(date);
      estDate.setHours(hour24, minutes, 0, 0);
      
      // The times are already in EST, so we don't need timezone conversion
      return estDate;
    } catch (error) {
      console.error(`[EarningsLost] Error parsing time ${timeStr}:`, error);
      throw error;
    }
  };

  // Helper function to calculate daylight downtime across potentially multiple days
  const calculateDaylightDowntime = async (lastStatusChangeTime: Date, now: Date, systemId: string): Promise<number> => {
    console.log(`[EarningsLost] Calculating daylight downtime from ${lastStatusChangeTime.toISOString()} to ${now.toISOString()}`);
    
    let totalDaylightDowntime = 0; // in milliseconds
    
    // Start from the day the inverter went red
    let currentDay = new Date(lastStatusChangeTime);
    currentDay.setHours(0, 0, 0, 0); // Start of day
    
    while (currentDay <= now) {
      const dateStr = currentDay.toISOString().split('T')[0];
      console.log(`[EarningsLost] Processing day ${dateStr} for daylight downtime`);
      
      try {
        // Get sunrise/sunset for this day
        const systemDailyData = await getConsolidatedDailyData(systemId, dateStr);
        const sunrise = systemDailyData?.sunrise || "5:31 AM";
        const sunset = systemDailyData?.sunset || "8:44 PM";
        
        console.log(`[EarningsLost] Day ${dateStr} - sunrise: ${sunrise}, sunset: ${sunset}`);
        
        // Parse sunrise/sunset to Date objects
        const daySunrise = parseTimeToEST(sunrise, currentDay);
        const daySunset = parseTimeToEST(sunset, currentDay);
        
        // Calculate the red period for this specific day
        const dayStart = new Date(currentDay);
        const dayEnd = new Date(currentDay);
        dayEnd.setHours(23, 59, 59, 999);
        
        const redStartThisDay = new Date(Math.max(lastStatusChangeTime.getTime(), dayStart.getTime()));
        const redEndThisDay = new Date(Math.min(now.getTime(), dayEnd.getTime()));
        
        // Calculate intersection with daylight hours
        const intersectionStart = new Date(Math.max(redStartThisDay.getTime(), daySunrise.getTime()));
        const intersectionEnd = new Date(Math.min(redEndThisDay.getTime(), daySunset.getTime()));
        
        if (intersectionStart < intersectionEnd) {
          const daylightDowntimeThisDay = intersectionEnd.getTime() - intersectionStart.getTime();
          totalDaylightDowntime += daylightDowntimeThisDay;
          
          console.log(`[EarningsLost] Day ${dateStr} daylight downtime:`, {
            redPeriod: `${redStartThisDay.toISOString()} to ${redEndThisDay.toISOString()}`,
            daylightPeriod: `${daySunrise.toISOString()} to ${daySunset.toISOString()}`,
            intersection: `${intersectionStart.toISOString()} to ${intersectionEnd.toISOString()}`,
            daylightDowntimeHours: (daylightDowntimeThisDay / (1000 * 60 * 60)).toFixed(2)
          });
        } else {
          console.log(`[EarningsLost] Day ${dateStr} - no daylight downtime (red period outside daylight hours)`);
        }
        
      } catch (error) {
        console.error(`[EarningsLost] Error getting sunrise/sunset for day ${dateStr}, using fallback:`, error);
        
        // Fallback to hardcoded times
        const daySunrise = parseTimeToEST("5:31 AM", currentDay);
        const daySunset = parseTimeToEST("8:44 PM", currentDay);
        
        // Calculate using fallback times (same logic as above)
        const dayStart = new Date(currentDay);
        const dayEnd = new Date(currentDay);
        dayEnd.setHours(23, 59, 59, 999);
        
        const redStartThisDay = new Date(Math.max(lastStatusChangeTime.getTime(), dayStart.getTime()));
        const redEndThisDay = new Date(Math.min(now.getTime(), dayEnd.getTime()));
        
        const intersectionStart = new Date(Math.max(redStartThisDay.getTime(), daySunrise.getTime()));
        const intersectionEnd = new Date(Math.min(redEndThisDay.getTime(), daySunset.getTime()));
        
        if (intersectionStart < intersectionEnd) {
          const daylightDowntimeThisDay = intersectionEnd.getTime() - intersectionStart.getTime();
          totalDaylightDowntime += daylightDowntimeThisDay;
          
          console.log(`[EarningsLost] Day ${dateStr} daylight downtime (fallback):`, {
            daylightDowntimeHours: (daylightDowntimeThisDay / (1000 * 60 * 60)).toFixed(2)
          });
        }
      }
      
      // Move to next day
      currentDay.setDate(currentDay.getDate() + 1);
    }
    
    const totalHours = totalDaylightDowntime / (1000 * 60 * 60);
    console.log(`[EarningsLost] Total daylight downtime calculated: ${totalHours.toFixed(2)} hours (${totalDaylightDowntime} ms)`);
    
    return totalDaylightDowntime;
  };

  // Helper function to get last 5 days with actual earnings data before the inverter went red
  const getLastValidEarningsDays = async (inverterId: string, lastStatusChangeTime: Date, maxDays: number = 15): Promise<string[]> => {
    console.log(`[EarningsLost] Searching for last 5 valid earnings days for inverter ${inverterId} within ${maxDays} days before ${lastStatusChangeTime.toISOString()}`);
    const startTime = Date.now();
    
    // Generate date range to search - starting from the day BEFORE lastStatusChangeTime and going backwards
    const startDate = new Date(lastStatusChangeTime);
    startDate.setDate(startDate.getDate() - 1); // Start from day before it went red
    
    const dates = Array.from({length: maxDays}, (_, i) => {
      const date = new Date(startDate);
      date.setDate(date.getDate() - i);
      return date.toISOString().split('T')[0];
    });
    
    console.log(`[EarningsLost] Generated ${maxDays} days range before inverter went red: ${dates[0]} to ${dates[dates.length - 1]}`);
    
    // Fetch all dates in parallel
    const dailyDataPromises = dates.map((date: string) => 
      getInverterDailyData(inverterId, date).catch((error) => {
        console.warn(`[EarningsLost] Failed to get daily data for inverter ${inverterId} on ${date}:`, error);
        return null;
      })
    );
    
    const dailyDataResults = await Promise.all(dailyDataPromises);
    
    // Filter for days with earnings > 0 and take the first 5 (most recent before going red)
    const validDays = dailyDataResults
      .map((data, index: number) => ({ data, date: dates[index] }))
      .filter(item => item.data && item.data.earnings > 0)
      .slice(0, 5) // Take first 5 (most recent before going red)
      .map(item => item.date);
    
    const searchTime = Date.now() - startTime;
    console.log(`[EarningsLost] Valid earnings days search for inverter ${inverterId}:`, {
      searchTimeMs: searchTime,
      daysSearched: maxDays,
      validDaysFound: validDays.length,
      validDates: validDays,
      searchStartedFrom: startDate.toISOString().split('T')[0]
    });
    
    return validDays;
  };

  // Helper function to calculate earnings lost for a single inverter
  const calculateInverterEarningsLost = async (inverterId: string): Promise<{earningsLost: number, lastStatusChangeTime: Date}> => {
    console.log(`[EarningsLost] Starting calculation for inverter: ${inverterId}`);
    const startTime = Date.now();
    
    try {
      // Step 1: Get inverter status to find lastStatusChangeTime
      console.log(`[EarningsLost] Fetching status for inverter ${inverterId}`);
      const inverterStatus = await getInverterStatus(inverterId);
      const lastStatusChangeTime = new Date(inverterStatus.lastStatusChangeTime);
      console.log(`[EarningsLost] Inverter ${inverterId} last status change: ${lastStatusChangeTime.toISOString()}`);
      
      // Step 2: Get last 5 days with actual earnings data before the inverter went red and calculate average daily earnings
      const validDays = await getLastValidEarningsDays(inverterId, lastStatusChangeTime);
      console.log(`[EarningsLost] Found ${validDays.length} valid earnings days for inverter ${inverterId}: ${validDays.join(', ')}`);
      
      if (validDays.length === 0) {
        console.warn(`[EarningsLost] No valid earnings days found for inverter ${inverterId}`);
        return { earningsLost: 0, lastStatusChangeTime };
      }
      
      // Fetch the data for the valid days
      const dailyDataPromises = validDays.map((date: string) => 
        getInverterDailyData(inverterId, date).catch((error) => {
          console.warn(`[EarningsLost] Failed to get daily data for inverter ${inverterId} on ${date}:`, error);
          return null;
        })
      );
      
      const dailyDataResults = await Promise.all(dailyDataPromises);
      console.log(`[EarningsLost] Daily data results for inverter ${inverterId}:`, dailyDataResults.map((data, index: number) => ({
        date: validDays[index],
        hasData: data !== null,
        earnings: data?.earnings || 0
      })));
      
      const validDailyData = dailyDataResults.filter((data) => data !== null && data.earnings > 0) as InverterDailyData[];
      
      const totalEarnings = validDailyData.reduce((sum, data) => sum + data.earnings, 0);
      const averageDailyEarnings = totalEarnings / validDailyData.length;
      
      console.log(`[EarningsLost] Inverter ${inverterId} earnings calculation:`, {
        validDaysCount: validDailyData.length,
        totalEarnings: totalEarnings.toFixed(4),
        averageDailyEarnings: averageDailyEarnings.toFixed(4)
      });
      
      // Step 3: Get today's daylight hours (sunrise to sunset) for accurate per-minute calculation
      const today = new Date();
      const todayStr = today.toISOString().split('T')[0];
      
      let daylightHours = 15; // Fallback to 15 hours if API fails
      try {
        const todaySystemData = await getConsolidatedDailyData(systemId, todayStr);
        const sunrise = todaySystemData?.sunrise || "5:31 AM";
        const sunset = todaySystemData?.sunset || "8:44 PM";
        
        console.log(`[EarningsLost] Today's daylight hours - sunrise: ${sunrise}, sunset: ${sunset}`);
        
        // Parse sunrise/sunset to calculate daylight hours
        const todaySunrise = parseTimeToEST(sunrise, today);
        const todaySunset = parseTimeToEST(sunset, today);
        const daylightMs = todaySunset.getTime() - todaySunrise.getTime();
        daylightHours = daylightMs / (1000 * 60 * 60); // Convert to hours
        
        console.log(`[EarningsLost] Calculated daylight hours for ${todayStr}: ${daylightHours.toFixed(2)} hours`);
      } catch (error) {
        console.warn(`[EarningsLost] Failed to get today's sunrise/sunset, using fallback ${daylightHours} hours:`, error);
      }
      
      // Step 4: Calculate earnings per minute using actual daylight hours
      const earningsPerMinute = averageDailyEarnings / (daylightHours * 60);
      console.log(`[EarningsLost] Inverter ${inverterId} earnings calculation:`, {
        averageDailyEarnings: `$${averageDailyEarnings.toFixed(4)}`,
        daylightHours: daylightHours.toFixed(2),
        earningsPerMinute: `$${earningsPerMinute.toFixed(6)}`,
        oldCalculation24h: `$${(averageDailyEarnings / (24 * 60)).toFixed(6)}`,
        improvementFactor: `${(daylightHours / 24).toFixed(2)}x more accurate`
      });
      
      // Step 5: Calculate DAYLIGHT downtime and earnings lost
      const nowUtc = new Date();
      const daylightDowntimeMs = await calculateDaylightDowntime(lastStatusChangeTime, nowUtc, systemId);
      const daylightDowntimeMinutes = daylightDowntimeMs / (1000 * 60);
      
      console.log(`[EarningsLost] Inverter ${inverterId} daylight downtime calculation:`, {
        currentTime: nowUtc.toISOString(),
        lastStatusChange: lastStatusChangeTime.toISOString(),
        daylightDowntimeMinutes: daylightDowntimeMinutes.toFixed(1),
        daylightDowntimeHours: (daylightDowntimeMinutes / 60).toFixed(1),
        daylightDowntimeDays: (daylightDowntimeMinutes / (60 * 24)).toFixed(1)
      });
      
      if (daylightDowntimeMinutes <= 0) {
        console.log(`[EarningsLost] Inverter ${inverterId} has no daylight downtime (${daylightDowntimeMinutes.toFixed(1)} minutes)`);
        return { earningsLost: 0, lastStatusChangeTime };
      }
      
      const earningsLost = daylightDowntimeMinutes * earningsPerMinute;
      const calculationTime = Date.now() - startTime;
      
      console.log(`[EarningsLost] Inverter ${inverterId} final calculation:`, {
        averageDailyEarnings: `$${averageDailyEarnings.toFixed(4)}`,
        daylightHours: daylightHours.toFixed(2),
        earningsPerMinute: `$${earningsPerMinute.toFixed(6)}`,
        daylightDowntimeMinutes: daylightDowntimeMinutes.toFixed(1),
        earningsLost: `$${earningsLost.toFixed(4)}`,
        calculationTimeMs: calculationTime,
        accuracyNote: `Using ${daylightHours.toFixed(1)}h daylight vs 24h assumption`
      });
      
      return { earningsLost, lastStatusChangeTime };
      
    } catch (error) {
      const calculationTime = Date.now() - startTime;
      console.error(`[EarningsLost] Error calculating earnings lost for inverter ${inverterId} (took ${calculationTime}ms):`, error);
      // Still return the lastStatusChangeTime even if calculation fails
      try {
        const inverterStatus = await getInverterStatus(inverterId);
        return { earningsLost: 0, lastStatusChangeTime: new Date(inverterStatus.lastStatusChangeTime) };
      } catch {
        return { earningsLost: 0, lastStatusChangeTime: new Date() };
      }
    }
  };

  // Main calculation function
  const calculateTotalEarningsLost = async () => {
    console.log(`[EarningsLost] Starting total earnings lost calculation for system ${systemId}`);
    const startTime = Date.now();
    
    setIsLoading(true);
    setError(null);
    setEarliestDownTime(null);
    
    try {
      // Step 1: Get system status to find red inverters
      console.log(`[EarningsLost] Fetching system status details for system ${systemId}`);
      const systemStatusDetails = await getSystemStatusDetails(systemId);
      const redInverters = systemStatusDetails.RedInverters || [];
      
      console.log(`[EarningsLost] System ${systemId} red inverters:`, {
        count: redInverters.length,
        inverterIds: redInverters
      });
      
      if (redInverters.length === 0) {
        console.log(`[EarningsLost] No red inverters found for system ${systemId}, setting loss to $0`);
        setTotalEarningsLost(0);
        setIsLoading(false);
        return;
      }
      
      // Step 2: Calculate earnings lost for each red inverter
      console.log(`[EarningsLost] Calculating earnings lost for ${redInverters.length} red inverters`);
      const earningsLostPromises = redInverters.map(inverterId => 
        calculateInverterEarningsLost(inverterId)
      );
      
      const earningsLostResults = await Promise.all(earningsLostPromises);
      const totalLoss = earningsLostResults.reduce((sum, loss) => sum + loss.earningsLost, 0);
      
      // Find the earliest lastStatusChangeTime (when the first inverter went red)
      const earliestTime = earningsLostResults
        .map(result => result.lastStatusChangeTime)
        .reduce((earliest, current) => current < earliest ? current : earliest);
      
      setEarliestDownTime(earliestTime);
      
      const calculationTime = Date.now() - startTime;
      console.log(`[EarningsLost] Total calculation completed for system ${systemId}:`, {
        redInvertersCount: redInverters.length,
        individualLosses: earningsLostResults.map((loss, index) => ({
          inverterId: redInverters[index],
          loss: `$${loss.earningsLost.toFixed(4)}`
        })),
        totalLoss: `$${totalLoss.toFixed(4)}`,
        earliestDownTime: earliestTime.toISOString(),
        calculationTimeMs: calculationTime
      });
      
      setTotalEarningsLost(totalLoss);
      
    } catch (error) {
      const calculationTime = Date.now() - startTime;
      console.error(`[EarningsLost] Error calculating total earnings lost for system ${systemId} (took ${calculationTime}ms):`, error);
      setError('Failed to calculate earnings lost');
    } finally {
      setIsLoading(false);
    }
  };

  // Calculate earnings lost when component mounts or systemId changes
  useEffect(() => {
    console.log(`[EarningsLost] useEffect triggered for systemId: ${systemId}`);
    calculateTotalEarningsLost();
  }, [systemId]);

  // Log state changes
  useEffect(() => {
    console.log(`[EarningsLost] State update - isLoading: ${isLoading}, error: ${error}, totalEarningsLost: $${totalEarningsLost.toFixed(2)}, earliestDownTime: ${earliestDownTime?.toISOString() || 'null'}`);
  }, [isLoading, error, totalEarningsLost, earliestDownTime]);

  if (isLoading) {
    console.log(`[EarningsLost] Rendering loading state for system ${systemId}`);
    return (
      <View style={[
        styles.container,
        { 
          backgroundColor: isDarkMode ? 'rgba(244, 67, 54, 0.1)' : 'rgba(244, 67, 54, 0.05)',
          borderColor: 'rgba(244, 67, 54, 0.3)'
        }
      ]}>
        <ActivityIndicator size="small" color="#F44336" />
        <Text style={[styles.loadingText, { color: colors.text }]}>
          Calculating...
        </Text>
      </View>
    );
  }

  if (error) {
    console.log(`[EarningsLost] Rendering error state for system ${systemId}: ${error}`);
    return (
      <View style={[
        styles.container,
        { 
          backgroundColor: isDarkMode ? 'rgba(244, 67, 54, 0.1)' : 'rgba(244, 67, 54, 0.05)',
          borderColor: 'rgba(244, 67, 54, 0.3)'
        }
      ]}>
        <Ionicons name="warning" size={16} color="#F44336" />
        <Text style={[styles.errorText, { color: '#F44336' }]}>
          {error}
        </Text>
      </View>
    );
  }

  console.log(`[EarningsLost] Rendering final result for system ${systemId}: $${totalEarningsLost.toFixed(2)}`);
  return (
    <View style={[
      styles.container,
      { 
        backgroundColor: isDarkMode ? 'rgba(244, 67, 54, 0.1)' : 'rgba(244, 67, 54, 0.05)',
        borderColor: 'rgba(244, 67, 54, 0.3)'
      }
    ]}>
      <View style={styles.iconContainer}>
        <Ionicons 
          name="trending-down" 
          size={16} 
          color="#F44336" 
        />
      </View>
      
      <View style={styles.textContainer}>
        <Text style={[
          styles.titleText,
          { color: '#F44336' }
        ]}>
          Estimated Loss
        </Text>
        <Text style={[
          styles.amountText,
          { color: colors.text }
        ]}>
          ${totalEarningsLost.toFixed(2)}
        </Text>
        {earliestDownTime && (
          <Text style={[
            styles.sinceText,
            { color: colors.tabIconDefault }
          ]}>
            Since: {formatSinceDate(earliestDownTime)}
          </Text>
        )}
      </View>
  
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginTop: 8,
    borderRadius: 8,
    borderWidth: 1,
    marginHorizontal: 4,
  },
  iconContainer: {
    marginRight: 8,
  },
  textContainer: {
    flex: 1,
  },
  titleText: {
    fontSize: 12,
    fontWeight: '600',
    marginBottom: 2,
  },
  amountText: {
    fontSize: 16,
    fontWeight: 'bold',
  },
  statusContainer: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginRight: 4,
  },
  statusText: {
    fontSize: 11,
    fontWeight: '500',
  },
  loadingText: {
    fontSize: 12,
    fontWeight: '500',
    marginLeft: 8,
  },
  errorText: {
    fontSize: 12,
    fontWeight: '500',
    marginLeft: 8,
  },
  sinceText: {
    fontSize: 10,
    marginTop: 4,
  },
});

export default EarningsLost; 