import { WorkflowVersionResponse } from './workflowSchemas';

export type RunStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';

export interface TaskResponse {
  id: string;
  run_id: string;
  node_id: string;
  status: RunStatus;
  start_time?: string;
  end_time?: string;
}

export interface StartRunRequest {
  initial_inputs?: Record<string, Record<string, any>>;
  parent_run_id?: string;
}

export interface RunResponse {
  id: string;
  workflow_id: string;
  workflow_version_id: number;
  workflow_version: WorkflowVersionResponse;
  status: RunStatus;
  run_type: string;
  initial_inputs?: Record<string, Record<string, any>>;
  input_dataset_id?: string;
  outputs?: Record<string, any>;
  output_file_id?: string;
  start_time?: string;
  end_time?: string;
  tasks: TaskResponse[];
}

export interface PartialRunRequest {
  node_id: string;
  rerun_predecessors: boolean;
  initial_inputs?: Record<string, Record<string, any>>;
  partial_outputs?: Record<string, Record<string, any>>;
}

export interface BatchRunRequest {
  dataset_id: string;
  mini_batch_size: number;
}
