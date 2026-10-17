/* ==================================================================== */
/* SAP/Sybase ASE Ultimate Comprehensive Structural Schema Dump          */
/* Scope: Every Data Type, Storage Constraint, Index Pattern, Rule,    */
/*        View, Trigger, Function, and Stored Procedure Discussed.      */
/* ==================================================================== */

USE master
GO

IF EXISTS (SELECT 1 FROM sysdatabases WHERE name = 'extreme_adventure_db')
BEGIN
    DROP DATABASE extreme_adventure_db
END
GO

CREATE DATABASE extreme_adventure_db ON default = "150M" LOG ON default = "40M"
GO

USE extreme_adventure_db
GO

/* ==================================================================== */
/* 1. USER DEFINED DATATYPES (UDDs)                                     */
/* ==================================================================== */
EXEC sp_addtype 'u_member_code', 'varchar(20)', 'NOT NULL'
GO
EXEC sp_addtype 'u_status_flag', 'char(1)', 'NOT NULL'
GO
EXEC sp_addtype 'u_currency', 'numeric(15,4)', 'NULL'
GO

/* ==================================================================== */
/* 2. STANDALONE RULES & DEFAULTS (Decoupled Objects)                  */
/* ==================================================================== */

/* Standalone default object */
CREATE DEFAULT dbo.df_zero_currency AS 0.0000
GO

/* Standalone validation rule mask */
CREATE RULE dbo.rl_postal_code
AS @postal_code LIKE '[0-9][0-9][0-9][0-9][0-9]'
GO

/* ==================================================================== */
/* 3. TABLE STRUCTURES (Advanced Types, PKs, Checks, & Locking)        */
/* ==================================================================== */

/* --- Table: Customers (Uses Datapages Locking) --- */
CREATE TABLE dbo.Customers (
    CustomerID   int IDENTITY,
    MemberCode   u_member_code,
    CustomerName varchar(100) NOT NULL,
    PostalCode   char(10)     NULL, /* Standard rule bound to this later */
    StatusFlag   u_status_flag DEFAULT 'A',
    CreateDate   datetime      DEFAULT getdate() NOT NULL,
    /* Inline Column Check Constraint */
    CONSTRAINT chk_customer_status CHECK (StatusFlag IN ('A', 'I', 'P')),
    /* Clustered Primary Key */
    CONSTRAINT pk_customers PRIMARY KEY CLUSTERED (CustomerID)
)
LOCK DATAPAGES
GO

/* --- Table: Products (Uses Datarows Locking + Unsigned & Unicode Types) --- */
CREATE TABLE dbo.Products (
    ProductID        int             NOT NULL,
    ProductCode      varchar(25)     NOT NULL,
    ProductName      univarchar(150) NOT NULL, -- Unicode variable string (UTF-16)
    UnsignedStock    uint            DEFAULT 0, -- Unsigned Integer (Sybase specific)
    ListPrice        u_currency      NULL,     -- Bound to external default later
    IsActive         bit             DEFAULT 1 NOT NULL,
    /* Materialized Computed Column (Saved to disk) */
    TaxedPriceMarkup AS (ListPrice * 1.15) MATERIALIZED,
    /* Inline Unique Constraint */
    CONSTRAINT uq_product_code UNIQUE NONCLUSTERED (ProductCode),
    /* Table-level Check Constraint */
    CONSTRAINT chk_product_pricing CHECK (ListPrice >= 0.0000),
    CONSTRAINT pk_products PRIMARY KEY CLUSTERED (ProductID)
)
LOCK DATAROWS
GO

/* --- Table: Orders (Uses Traditional Allpages Locking + Large Objects) --- */
CREATE TABLE dbo.Orders (
    OrderID          bigint IDENTITY,          -- Bigint testing
    CustomerID       int             NOT NULL,
    OrderDate        bigdatetime     DEFAULT getdate() NOT NULL, -- High-precision time
    TotalAmount      u_currency      NOT NULL,
    OrderStatus      u_status_flag   DEFAULT 'P',
    OrderNotesLOB    unitext         NULL,     -- Large Unicode Object
    RawXMLPayload    xml             NULL,     -- Native XML storage type
    SybaseTimeStamp  timestamp       NOT NULL, -- Auto-incrementing binary sequence
    CONSTRAINT chk_order_status CHECK (OrderStatus IN ('P', 'S', 'C', 'R')),
    CONSTRAINT pk_orders PRIMARY KEY CLUSTERED (OrderID)
)
LOCK ALLPAGES
GO

/* --- Table: OrderDetails (Child Table) --- */
CREATE TABLE dbo.OrderDetails (
    OrderDetailID int IDENTITY,
    OrderID       bigint     NOT NULL,
    ProductID     int        NOT NULL,
    Quantity      int        NOT NULL,
    UnitPrice     u_currency NOT NULL,
    CONSTRAINT chk_quantity_positive CHECK (Quantity > 0),
    CONSTRAINT pk_orderdetails PRIMARY KEY CLUSTERED (OrderDetailID)
)
LOCK DATAROWS
GO

/* ==================================================================== */
/* 4. OBJECT BINDING (Attaching Standalone Decoupled Items)             */
/* ==================================================================== */

EXEC sp_bindefault 'dbo.df_zero_currency', 'dbo.Products.ListPrice'
GO

EXEC sp_bindrule 'dbo.rl_postal_code', 'dbo.Customers.PostalCode'
GO

/* ==================================================================== */
/* 5. INDEXES (Composite, Covered, & Storage Settings)                 */
/* ==================================================================== */

/* Composite non-clustered index on parent table */
CREATE NONCLUSTERED INDEX idx_customers_search
    ON dbo.Customers (StatusFlag, CustomerName)
GO

/* Single column index */
CREATE NONCLUSTERED INDEX idx_orders_customer
    ON dbo.Orders (CustomerID)
GO

/* Covered performance index with sorted columns and custom fillfactor */
CREATE NONCLUSTERED INDEX idx_details_composite
    ON dbo.OrderDetails (OrderID ASC, ProductID ASC)
    WITH FILLFACTOR = 80
GO

/* ==================================================================== */
/* 6. DECLARATIVE REFERENTIAL INTEGRITY (FOREIGN KEYS)                 */
/* ==================================================================== */

ALTER TABLE dbo.Orders
    ADD CONSTRAINT fk_orders_customers FOREIGN KEY (CustomerID)
    REFERENCES dbo.Customers(CustomerID)
GO

ALTER TABLE dbo.OrderDetails
    ADD CONSTRAINT fk_details_orders FOREIGN KEY (OrderID)
    REFERENCES dbo.Orders(OrderID)
GO

ALTER TABLE dbo.OrderDetails
    ADD CONSTRAINT fk_details_products FOREIGN KEY (ProductID)
    REFERENCES dbo.Products(ProductID)
GO

/* ==================================================================== */
/* 7. USER DEFINED FUNCTIONS (UDFs)                                     */
/* ==================================================================== */

/* Sybase ASE scalar function */
CREATE FUNCTION dbo.fn_CalculateTax (
    @Amount u_currency,
    @TaxRate numeric(5,2)
)
RETURNS u_currency
AS
BEGIN
    DECLARE @CalculatedTax u_currency

    IF @Amount IS NULL
        SELECT @CalculatedTax = 0.0000
    ELSE
        SELECT @CalculatedTax = @Amount * (@TaxRate / 100.00)

    RETURN @CalculatedTax
END
GO

/* ==================================================================== */
/* 8. VIEWS (Referencing Functions & Computed Columns)                  */
/* ==================================================================== */

CREATE VIEW dbo.vw_OrderFinancialSummary AS
SELECT
    o.OrderID,
    c.CustomerName,
    c.MemberCode,
    o.OrderDate,
    o.TotalAmount,
    p.TaxedPriceMarkup, -- Testing view mapping over computed row
    dbo.fn_CalculateTax(o.TotalAmount, 8.25) AS EstimatedTax,
    o.OrderStatus,
    char_length(p.ProductName) AS UnicodeLengthName -- Testing built-in functions
FROM
    dbo.Orders o
    INNER JOIN dbo.Customers c ON o.CustomerID = c.CustomerID
    INNER JOIN dbo.OrderDetails od ON o.OrderID = od.OrderID
    INNER JOIN dbo.Products p ON od.ProductID = p.ProductID
GO

/* ==================================================================== */
/* 9. TRIGGERS (Using Native Inserted / Deleted Architecture)           */
/* ==================================================================== */

CREATE TRIGGER dbo.trg_OrderDetails_InsertValidation
ON dbo.OrderDetails
FOR INSERT
AS
BEGIN
    /* Block additions if the product is flagged inactive */
    IF EXISTS (
        SELECT 1
        FROM inserted i
        INNER JOIN dbo.Products p ON i.ProductID = p.ProductID
        WHERE p.IsActive = 0
    )
    BEGIN
        RAISERROR 99999 "Transaction Aborted: Cannot add inactive products to an active sales order line item."
        ROLLBACK TRANSACTION
    END
END
GO

/* ==================================================================== */
/* 10. STORED PROCEDURES (Variables, Conditional Splits, Output Params) */
/* ==================================================================== */

CREATE PROCEDURE dbo.sp_GetCustomerMetrics
    @MinOrderValue u_currency = 0.0000,
    @StatusFilter   u_status_flag = NULL,
    @TotalCount     int OUTPUT
AS
BEGIN
    SET NOCOUNT ON
    DECLARE @ErrCode int

    /* Calculate output aggregate variable */
    SELECT @TotalCount = COUNT(DISTINCT o.CustomerID)
    FROM dbo.Orders o
    WHERE o.TotalAmount >= @MinOrderValue

    SELECT @ErrCode = @@error
    IF @ErrCode != 0
    BEGIN
        PRINT "Database internal processing error calculation failed."
        RETURN @ErrCode
    END

    /* Run conditional branch blocks */
    IF @StatusFilter IS NULL
    BEGIN
        SELECT
            c.CustomerID,
            c.CustomerName,
            SUM(o.TotalAmount) AS LifetimeValue,
            COUNT(o.OrderID) AS OrderVolume
        FROM dbo.Customers c
        INNER JOIN dbo.Orders o ON c.CustomerID = o.CustomerID
        GROUP BY c.CustomerID, c.CustomerName
        HAVING SUM(o.TotalAmount) >= @MinOrderValue
        ORDER BY LifetimeValue DESC
    END
    ELSE
    BEGIN
        SELECT
            c.CustomerID,
            c.CustomerName,
            SUM(o.TotalAmount) AS LifetimeValue,
            COUNT(o.OrderID) AS OrderVolume
        FROM dbo.Customers c
        INNER JOIN dbo.Orders o ON c.CustomerID = o.CustomerID
        WHERE c.StatusFlag = @StatusFilter
        GROUP BY c.CustomerID, c.CustomerName
        HAVING SUM(o.TotalAmount) >= @MinOrderValue
        ORDER BY LifetimeValue DESC
    END

    RETURN 0
END
GO
