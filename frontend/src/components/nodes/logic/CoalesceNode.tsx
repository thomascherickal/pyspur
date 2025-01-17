import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Handle, Position, useConnection } from '@xyflow/react';
import BaseNode from '../BaseNode';
import {
  Input,
  Card,
  Divider,
  Button,
  Select,
  SelectItem
} from '@nextui-org/react';
import { useDispatch, useSelector } from 'react-redux';
import { updateNodeData } from '../../../store/flowSlice';
import styles from '../DynamicNode.module.css';
import { Icon } from '@iconify/react';
import { RootState } from '../../../store/store';
import NodeOutputDisplay from '../NodeOutputDisplay';
import isEqual from 'lodash/isEqual';
import { FlowWorkflowNode } from '../../../store/flowSlice';

interface CoalesceNodeProps {
  id: string;
  data: FlowWorkflowNode['data'];
  selected?: boolean;
}

/**
 * A node that displays multiple input handles (one for each upstream node),
 * and a preference list allowing the user to pick fields in a certain priority order.
 */
export const CoalesceNode: React.FC<CoalesceNodeProps> = ({ id, data }) => {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const nodeRef = useRef<HTMLDivElement | null>(null);

  // We’ll dynamically compute a width that fits the labels (handles).
  const [nodeWidth, setNodeWidth] = useState<string>('auto');

  const dispatch = useDispatch();
  const connection = useConnection();

  // Retrieve all nodes & edges from Redux so we can figure out which are predecessors
  const nodes = useSelector((state: RootState) => state.flow.nodes);
  const edges = useSelector((state: RootState) => state.flow.edges);

  // Node's output for display
  const nodeOutput = useSelector(
    (state: RootState) => state.flow.nodes.find((node) => node.id === id)?.data?.run
  );

  // The CoalesceNode might have multiple incoming edges. We'll track those predecessor nodes (if any).
  const [predecessorNodes, setPredecessorNodes] = useState(
    edges
      .filter((edge) => edge.target === id)
      .map((edge) => nodes.find((node) => node.id === edge.source))
      .filter(Boolean)
  );

  // Add a type guard to check if the node is a FlowWorkflowNode
  const isFlowWorkflowNode = (node: any): node is FlowWorkflowNode => {
    return 'type' in node;
  };

  // Recompute predecessor nodes whenever edges/connections change
  useEffect(() => {
    const updatedPredecessors = edges
      .filter((edge) => edge.target === id)
      .map((edge) => nodes.find((node) => node.id === edge.source))
      .filter(Boolean);

    let finalPredecessors = updatedPredecessors;

    // If a new connection is in progress to this node, show that source node as well
    if (connection.inProgress && connection.toNode?.id === id && connection.fromNode) {
      const existing = finalPredecessors.find((p) => p?.id === connection.fromNode?.id);
      if (!existing && isFlowWorkflowNode(connection.fromNode)) {
        finalPredecessors = [...finalPredecessors, connection.fromNode];
      }
    }

    // Deduplicate
    finalPredecessors = finalPredecessors.filter((node, index, self) => {
      return self.findIndex((n) => n?.id === node?.id) === index;
    });

    // Compare to existing predecessorNodes; only set if changed
    const hasChanged =
      finalPredecessors.length !== predecessorNodes.length ||
      finalPredecessors.some(
        (node, i) => !isEqual(node, predecessorNodes[i])
      );
    if (hasChanged) {
      setPredecessorNodes(finalPredecessors);
    }
  }, [connection, edges, id, nodes, predecessorNodes]);

  /**
   * Build an array of upstream node IDs for the dropdown. 
   * (We’re not currently using the node’s output_schema to filter the keys.)
   */
  const inputVariables = useMemo(() => {
    return predecessorNodes
      .map((pred) => {
        if (!pred) return null;
        const label = pred.data?.config?.title || pred.id;
        return {
          value: pred.id,
          label
        };
      })
      .filter(Boolean) as { value: string; label: string }[];
  }, [predecessorNodes]);


  /**
   * Keep track of used variable preferences so we don't show duplicates in other slots.
   */
  const usedPreferences = data.config.preferences.filter(Boolean);
  const availableVariablesForIndex = (index: number) => {
    return inputVariables.filter(
      (v) => !usedPreferences.includes(v.value) || v.value === data.config.preferences[index]
    );
  };

  /** A helper to update the node's preference array in Redux */
  const updatePreferences = (newPreferences: string[]) => {
    dispatch(
      updateNodeData({
        id,
        data: {
          ...data,
          config: {
            ...data.config,
            preferences: newPreferences
          }
        }
      })
    );
  };

  // Keep preference array in sync with number of input variables
  useEffect(() => {
    const desiredLength = inputVariables.length;
    let updated = [...data.config.preferences];

    if (updated.length > desiredLength) {
      updated = updated.slice(0, desiredLength);
    } else if (updated.length < desiredLength) {
      while (updated.length < desiredLength) {
        updated.push('');
      }
    }

    const changed =
      updated.length !== data.config.preferences.length ||
      updated.some((val, i) => val !== data.config.preferences[i]);

    if (changed) {
      updatePreferences(updated);
    }
  }, [inputVariables]);

  /** Update preference index i to a new variable name  */
  const handlePreferenceChange = (index: number, value: string) => {
    const updated = [...data.config.preferences];
    updated[index] = value;
    updatePreferences(updated);
  };

  /** Clear the preference at index i */
  const clearPreference = (index: number) => {
    const updated = [...data.config.preferences];
    updated[index] = '';
    updatePreferences(updated);
  };

  /**
   *  Measure the lengths of input handle labels + output label 
   *  and set an appropriate nodeWidth so that names are fully visible.
   */
  useEffect(() => {
    if (!nodeRef.current) return;

    // We have multiple input handle labels
    const inputLabels = predecessorNodes.map(
      (pred) => pred?.data?.config?.title || pred?.id || ''
    );

    // Output label is the node's title or fallback
    const outputLabels = [data.config.title || 'Coalesce'];

    // Compute the max length among all input labels
    const maxInputLabelLength = inputLabels.reduce(
      (max, label) => Math.max(max, label.length),
      0
    );
    // Compute the max length among all output labels
    const maxOutputLabelLength = outputLabels.reduce(
      (max, label) => Math.max(max, label.length),
      0
    );

    // The node's own title (for the top of the node)
    const nodeTitle = data.config.title || 'Coalesce';
    const nodeTitleLength = nodeTitle.length;

    // Some extra spacing
    const buffer = 5;

    // Rough estimate: multiply the longest label length by ~10 for width in px.
    const minNodeWidth = 300;
    const maxNodeWidth = 600;

    const estimatedWidth = Math.max(
      (maxInputLabelLength + maxOutputLabelLength + buffer) * 10,
      nodeTitleLength * 10,
      minNodeWidth
    );
    const finalWidth = Math.min(estimatedWidth, maxNodeWidth);

    // If collapsed, show auto; otherwise the computed width
    if (nodeWidth !== `${finalWidth}px`) {
      setNodeWidth(isCollapsed ? 'auto' : `${finalWidth}px`);
    }
  }, [predecessorNodes, data.config.title, isCollapsed]);

  return (
    <BaseNode
      id={id}
      isCollapsed={isCollapsed}
      setIsCollapsed={setIsCollapsed}
      data={{
        title: data.config.title || 'Coalesce',
        color: data.color || '#38B2AC',
        acronym: 'CL',
        config: data.config,
        run: data.run,
        taskStatus: data.taskStatus
      }}
      // Use the computed nodeWidth
      style={{ width: nodeWidth }}
      className="hover:!bg-background"
    >
      <div className="p-3" ref={nodeRef}>
        {/** 
         * --------------------------
         * Top Row: Input + Output
         * --------------------------
         */}
        <div className="flex w-full items-start justify-between mb-4">
          {/* Left column: input handles */}
          <div>
            {predecessorNodes.map((node) => {
              if (!node) return null;
              const handleId = node.data?.config?.title || node.id;
              return (
                <div
                  key={node.id}
                  className={`${styles.handleRow} w-full justify-start mb-2`}
                >
                  <Handle
                    type="target"
                    position={Position.Left}
                    id={handleId}
                    className={`${styles.handle} ${styles.handleLeft} ${
                      isCollapsed ? styles.collapsedHandleInput : ''
                    }`}
                  />
                  {/* Show the full label if not collapsed */}
                  {!isCollapsed && (
                    <span className="text-sm font-medium ml-2 text-foreground">
                      {handleId}
                    </span>
                  )}
                </div>
              );
            })}
          </div>

          {/* Right column: output handle */}
          <div>
            <div className={`${styles.handleRow} w-full justify-end`}>
              {/* Show the label if not collapsed */}
              {!isCollapsed && (
                <div className="align-center flex flex-grow flex-shrink mr-2">
                  <span className="text-sm font-medium ml-auto text-foreground">
                    {data.config.title || 'Output'}
                  </span>
                </div>
              )}
              <Handle
                type="source"
                position={Position.Right}
                // Use node title for handle id
                id={data.config.title || id}
                className={`${styles.handle} ${styles.handleRight} ${
                  isCollapsed ? styles.collapsedHandleOutput : ''
                }`}
              />
            </div>
          </div>
        </div>

        {/* The main body, hidden if collapsed */}
        {!isCollapsed && (
          <>
            <Divider className="my-2" />
            <div className="flex items-center gap-2 mb-4">
              <span className="text-sm font-medium text-foreground">
                Preferences
              </span>
              <Divider className="flex-grow" />
            </div>

            <div className="flex flex-col gap-4">
              {data.config.preferences.map((prefValue, i) => (
                <Card
                  key={i}
                  classNames={{ base: 'bg-background border-default-200 p-2' }}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Select
                      aria-label="Select variable"
                      size="sm"
                      selectedKeys={prefValue ? [prefValue] : []}
                      placeholder="Select variable"
                      onChange={(e) => handlePreferenceChange(i, e.target.value)}
                      className="min-w-[200px]"
                      classNames={{
                        trigger: 'bg-default-100 dark:bg-default-50 min-h-unit-12 h-auto',
                        popoverContent: 'bg-background dark:bg-background'
                      }}
                      isMultiline
                    >
                      {availableVariablesForIndex(i).map((variable) => (
                        <SelectItem
                          key={variable.value}
                          value={variable.value}
                          textValue={variable.label}
                        >
                          <div className="whitespace-normal">
                            <span>{variable.label}</span>
                          </div>
                        </SelectItem>
                      ))}
                    </Select>

                    {prefValue && (
                      <Button
                        size="sm"
                        color="danger"
                        onClick={() => clearPreference(i)}
                        isIconOnly
                      >
                        <Icon icon="solar:trash-bin-trash-linear" width={18} />
                      </Button>
                    )}
                  </div>
                </Card>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Display node's output if it exists */}
      <NodeOutputDisplay output={nodeOutput} />
    </BaseNode>
  );
};
