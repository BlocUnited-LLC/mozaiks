import { DataTable } from './DataTable.jsx';

export function ResourceTable(props) {
  return <DataTable variant="resource" pagination={false} {...props} />;
}

export default ResourceTable;
