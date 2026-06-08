"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type TaskRow = {
  job_id: string;
  file_name: string | null;
  file_size: number;
  status: string;
  progress: number;
  download_url: string | null;
  created_at: string | null;
  error_message?: string | null;
};

export function useTasks(enabled: boolean) {
  return useQuery({
    queryKey: ["tasks"],
    enabled,
    queryFn: async (): Promise<TaskRow[]> => {
      const { data } = await api.get<{ tasks: TaskRow[] }>("/tasks");
      return data.tasks;
    },
    refetchInterval: (query) => {
      const tasks = query.state.data ?? [];
      const hasActive = tasks.some((t) =>
        ["PENDING", "SUBMITTED", "DOWNLOADING"].includes(t.status),
      );
      return hasActive ? 3000 : 30000;
    },
  });
}

export function useDeleteTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      await api.delete(`/tasks/${jobId}`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

export function useCleanupTasks() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.delete("/tasks/cleanup");
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}
