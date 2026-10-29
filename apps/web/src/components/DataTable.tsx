/** A table on TanStack Table: sortable columns, an optional row class, the rows a page has already filtered. */
import {
  type ColumnDef, flexRender, getCoreRowModel, getSortedRowModel, type SortingState, useReactTable,
} from "@tanstack/react-table";
import { useState } from "react";
import { cx } from "./ui";

export function DataTable<T>({ data, columns, rowClass, getRowId, empty = "Nothing to show." }: {
  data: T[];
  columns: ColumnDef<T, any>[];
  rowClass?: (row: T) => string | undefined;
  getRowId?: (row: T, index: number) => string;
  empty?: string;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data, columns, state: { sorting }, onSortingChange: setSorting, getRowId,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
  });
  return (
    <div className="overflow-x-auto rounded-md border border-zinc-200 dark:border-zinc-800">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-zinc-50 text-left text-xs text-zinc-500 dark:bg-zinc-900">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th key={h.id} className="border-b border-zinc-200 px-3 py-2 font-medium dark:border-zinc-800"
                    style={{ width: h.column.columnDef.size !== 150 ? h.column.columnDef.size : undefined }}>
                  {h.isPlaceholder ? null : h.column.getCanSort() ? (
                    <button className="inline-flex items-center gap-1 hover:text-zinc-900 dark:hover:text-zinc-100"
                            onClick={h.column.getToggleSortingHandler()}>
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      <span className="text-[10px]">{{ asc: "▲", desc: "▼" }[h.column.getIsSorted() as string] ?? ""}</span>
                    </button>
                  ) : flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className={cx("border-b border-zinc-100 align-top last:border-0 dark:border-zinc-800/60",
                                            rowClass?.(row.original))}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-3 py-2">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
              ))}
            </tr>
          ))}
          {!table.getRowModel().rows.length && (
            <tr><td colSpan={columns.length} className="px-3 py-6 text-center text-zinc-500">{empty}</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
